using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text.Json;

// Inspect metadata only: do not load the plugin, its dependencies, or execute its code.
try
{
    var inspectMod = args.Length == 2 && args[0] == "--mod";
    if (args.Length != 1 && !inspectMod) throw new ArgumentException("请提供插件 DLL 路径。");
    using var stream = File.OpenRead(args[inspectMod ? 1 : 0]);
    using var pe = new PEReader(stream);
    if (!pe.HasMetadata) throw new BadImageFormatException("DLL 不包含托管元数据。");
    var reader = pe.GetMetadataReader();
    if (inspectMod)
    {
        Console.WriteLine(JsonSerializer.Serialize(ReadModMetadata(pe, reader)));
        return 0;
    }
    if (reader.GetString(reader.GetAssemblyDefinition().Name) != "ConfigurationManager")
        throw new InvalidDataException("插件程序集身份不符合预期。");
    var versions = new List<string>();
    foreach (var handle in reader.TypeDefinitions)
    {
        var type = reader.GetTypeDefinition(handle);
        foreach (var attributeHandle in type.GetCustomAttributes())
        {
            var attribute = reader.GetCustomAttribute(attributeHandle);
            EntityHandle attributeType;
            switch (attribute.Constructor.Kind)
            {
                case HandleKind.MemberReference:
                    attributeType = reader.GetMemberReference((MemberReferenceHandle)attribute.Constructor).Parent;
                    break;
                case HandleKind.MethodDefinition:
                    attributeType = reader.GetMethodDefinition((MethodDefinitionHandle)attribute.Constructor).GetDeclaringType();
                    break;
                default:
                    continue;
            }
            string name, ns;
            if (attributeType.Kind == HandleKind.TypeReference)
            {
                var reference = reader.GetTypeReference((TypeReferenceHandle)attributeType);
                name = reader.GetString(reference.Name);
                ns = reader.GetString(reference.Namespace);
            }
            else if (attributeType.Kind == HandleKind.TypeDefinition)
            {
                var definition = reader.GetTypeDefinition((TypeDefinitionHandle)attributeType);
                name = reader.GetString(definition.Name);
                ns = reader.GetString(definition.Namespace);
            }
            else continue;
            if (ns != "BepInEx" || name != "BepInPlugin") continue;
            var blob = reader.GetBlobReader(attribute.Value);
            if (blob.ReadUInt16() != 1) throw new InvalidDataException("BepInPlugin 特性无效。");
            var guid = blob.ReadSerializedString();
            var pluginName = blob.ReadSerializedString();
            var version = blob.ReadSerializedString();
            if (guid != "com.bepis.bepinex.configurationmanager") continue;
            if (string.IsNullOrEmpty(pluginName) || string.IsNullOrEmpty(version))
                throw new InvalidDataException("缺少 ConfigurationManager 插件元数据。");
            versions.Add(version);
        }
    }
    if (versions.Count != 1) throw new InvalidDataException("应当恰好有一个 ConfigurationManager BepInPlugin 特性。");
    Console.WriteLine(JsonSerializer.Serialize(new { version = versions[0] }));
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine("插件元数据检查失败：" + error.GetType().Name);
    return 1;
}

static object ReadModMetadata(PEReader pe, MetadataReader reader)
{
    if (reader.GetString(reader.GetAssemblyDefinition().Name) != "HostUtilities")
        throw new InvalidDataException("MOD 程序集身份不符合预期。");
    var hasDebugTestApi = false;
    int? resourceFormat = null;
    foreach (var handle in reader.TypeDefinitions)
    {
        var type = reader.GetTypeDefinition(handle);
        if (reader.GetString(type.Namespace) != "HostUtilities") continue;
        var name = reader.GetString(type.Name);
        if (name == "UpdateTestApi") hasDebugTestApi = true;
        if (name != "UpdateCatalog") continue;
        foreach (var fieldHandle in type.GetFields())
        {
            var field = reader.GetFieldDefinition(fieldHandle);
            if (reader.GetString(field.Name) != "ResourceFormat" ||
                (field.Attributes & (FieldAttributes.Static | FieldAttributes.Literal)) !=
                (FieldAttributes.Static | FieldAttributes.Literal)) continue;
            var constantHandle = field.GetDefaultValue();
            if (constantHandle.IsNil) continue;
            var constant = reader.GetConstant(constantHandle);
            if (constant.TypeCode != ConstantTypeCode.Int32)
                throw new InvalidDataException("资源格式常量无效。");
            resourceFormat = reader.GetBlobReader(constant.Value).ReadInt32();
        }
    }
    if (resourceFormat is null or < 1)
        throw new InvalidDataException("缺少资源格式常量。");
    foreach (var handle in reader.ManifestResources)
    {
        var resource = reader.GetManifestResource(handle);
        if (reader.GetString(resource.Name) != "HostUtilities.current-mod-info.json") continue;
        if (!resource.Implementation.IsNil) throw new InvalidDataException("MOD 声明未嵌入 DLL。");
        var directory = pe.PEHeaders.CorHeader!.ResourcesDirectory;
        var offset = checked((int)resource.Offset);
        var data = pe.GetSectionData(directory.RelativeVirtualAddress).GetReader(offset, directory.Size - offset);
        using var declaration = JsonDocument.Parse(data.ReadBytes(data.ReadInt32()));
        return new
        {
            hasDebugTestApi,
            minResourceVersion = declaration.RootElement.GetProperty("minResourceVersion").GetString(),
            resourceFormat = resourceFormat.Value
        };
    }
    throw new InvalidDataException("缺少嵌入的 MOD 声明。");
}
