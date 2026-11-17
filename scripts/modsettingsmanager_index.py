"""校验 Release ZIP，并将单个 Mod Settings 对象合并到现有索引。"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from typing import NamedTuple
import urllib.parse
import urllib.request
import zipfile

from update_index import Api, ROOT, sync_indexes as sync_public_indexes

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_DLL_BYTES = 16 * 1024 * 1024


class ReleaseLink(NamedTuple):
    url: str
    repository: str
    tag: str
    name: str


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def numeric_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+){0,2}', value):
        raise ValueError('Mod Settings 版本必须包含 2 到 4 个数值段。')
    parts = tuple(map(int, value.split('.')))
    if any(part > 2147483647 for part in parts):
        raise ValueError('Mod Settings 版本数值段不能超过 Int32。')
    return parts + (0,) * (4 - len(parts))


def parse_link(value):
    if not isinstance(value, str):
        raise ValueError('请提供 GitHub Release ZIP 文件链接。')
    url = value.strip()
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError('Release 文件链接无效。')
    uri = urllib.parse.urlsplit(url)
    parts = uri.path.split('/')
    if (uri.scheme != 'https' or uri.netloc != 'github.com' or uri.query or uri.fragment
        or len(parts) != 7 or parts[3:5] != ['releases', 'download']
        or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in ('.', '..') for p in parts[1:3])):
        raise ValueError('链接必须符合 https://github.com/owner/repo/releases/download/tag/file.zip。')
    tag, name = (urllib.parse.unquote(p, errors='strict') for p in parts[5:7])
    if not tag.startswith('v'):
        raise ValueError('Release Tag 必须为小写 v 加 2 到 4 个数值段。')
    numeric_version(tag[1:])
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', parts[6], re.IGNORECASE):
        raise ValueError('ZIP 文件名只能包含英文字母、数字、下划线、点和连字符。')
    return ReleaseLink(url, '/'.join(parts[1:3]), tag, name)


def asset_metadata(api, link):
    release = api.request('GET', 'repos/' + link.repository + '/releases/tags/' + urllib.parse.quote(link.tag, safe=''))
    if release.get('draft') is not False or release.get('tag_name') != link.tag or type(release.get('id')) is not int:
        raise ValueError('请求的 Release 尚未公开，或其 Tag 与链接不一致。')
    matches = []
    page = 1
    while True:
        assets = api.request('GET', f'repos/{link.repository}/releases/{release["id"]}/assets?per_page=100&page={page}')
        for asset in assets:
            if asset.get('name') == link.name:
                matches.append(asset)
        if len(assets) < 100:
            break
        page += 1
    if len(matches) != 1 or matches[0].get('state') != 'uploaded':
        raise ValueError('应当只有一个与文件链接匹配的已上传附件。')
    asset = matches[0]
    official = parse_link(asset.get('browser_download_url'))
    if official.repository.casefold() != link.repository.casefold() or official[2:] != link[2:]:
        raise ValueError('API 附件 URL 与提供的文件链接不一致。')
    size, digest = asset.get('size'), asset.get('digest')
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES:
        raise ValueError('API 附件大小无效或超过 32 MiB。')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest) or digest[7:] == '0' * 64:
        raise ValueError('API 附件必须提供有效的 SHA256 摘要；不使用本地摘要回退。')
    changelog = release.get('body')
    if changelog is None:
        changelog = ''
    if not isinstance(changelog, str):
        raise ValueError('Release 更新日志必须是文本。')
    return {'tag':link.tag, 'url':link.url, 'size':size, 'sha256':digest[7:].lower(), 'changelog':changelog}


def download_package(url, expected_size):
    # Download public assets without passing the private index writer token to redirects.
    request = urllib.request.Request(url, headers={'User-Agent':'OC2-ModSettings-Index'})
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read(expected_size + 1)
    return body


def validate_zip_format(body, entries):
    # UpdatePackageExtractor reads ordinary single-volume ZIP offsets directly.
    end = body.rfind(b'PK\x05\x06', max(0, len(body) - 65557))
    if end < 0 or end + 22 > len(body):
        raise ValueError('ZIP 结束目录损坏。')
    disk, directory_disk, disk_count, count, directory_size, offset, comment = struct.unpack_from('<4H2IH', body, end + 4)
    if (disk or directory_disk or disk_count != count or count != len(entries) or count == 65535
        or offset + directory_size != end or end + 22 + comment != len(body)):
        raise ValueError('不支持多卷、Zip64 或目录偏移无效的 ZIP。')
    for entry in entries:
        central = struct.unpack_from('<4s6H3I5H2I', body, offset)
        if (central[0] != b'PK\x01\x02' or central[13] or 0xFFFFFFFF in (central[8], central[9], central[16])
            or central[16] != entry.header_offset):
            raise ValueError('不支持多卷、Zip64 或条目偏移无效的 ZIP。')
        offset += 46 + sum(central[10:13])
        signature, _, flags, compression = struct.unpack_from('<4s3H', body, entry.header_offset)
        if (signature != b'PK\x03\x04' or (flags | entry.flag_bits) & 1
            or compression != entry.compress_type or compression not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError('ZIP 条目加密、压缩方式不受支持或本地文件头不一致。')
    if offset != end:
        raise ValueError('ZIP 中央目录大小不匹配。')


def package_paths(entries):
    # Require portable, unambiguous paths: the client also runs on case-sensitive filesystems.
    paths, seen, tree = [], set(), {}
    for entry in entries:
        path = entry.orig_filename.replace('\\', '/')
        parts = path.rstrip('/').split('/')
        if (path.startswith('/') or any(not part or part in ('.', '..') or part.endswith((' ', '.'))
            or re.search(r'[\x00-\x1f\x7f:<>"|?*]', part)
            or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.IGNORECASE) for part in parts)
            or (not entry.flag_bits & 0x800 and not path.isascii())):
            raise ValueError('ZIP 包含非法、不安全或非 UTF-8 的条目路径。')
        path = '/'.join(parts)
        if path.casefold() in seen:
            raise ValueError('ZIP 包含重复或大小写冲突的条目。')
        seen.add(path.casefold())
        directory = entry.orig_filename.replace('\\', '/').endswith('/')
        for index in range(1, len(parts) + 1):
            current = '/'.join(parts[:index])
            node = (current, directory or index < len(parts))
            if current.casefold() in tree and tree[current.casefold()] != node:
                raise ValueError('ZIP 条目存在文件/目录或大小写冲突。')
            tree[current.casefold()] = node
        if not directory:
            paths.append((path, entry))
    return paths


def plugin_dll(body):
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as package:
            entries = package.infolist()
            validate_zip_format(body, entries)
            files = package_paths(entries)
            matches = [(path, entry) for path, entry in files if path.split('/')[-1].casefold() == 'oc2modsettings.dll']
            if len(matches) != 1:
                raise ValueError('ZIP 必须恰好包含一个 OC2ModSettings.dll。')
            path, entry = matches[0]
            source = path[:-len('OC2ModSettings.dll')]
            if (not path.endswith('OC2ModSettings.dll') or source not in
                ('', 'BepInEx/plugins/OC2ModSettings/', 'BepInEx/plugins/', 'OC2ModSettings/')):
                raise ValueError('ZIP 插件目录布局或文件名大小写不受客户端支持。')
            if any(not name.startswith(source) or (name != path and name.lower().endswith('.dll')) for name, _ in files):
                raise ValueError('ZIP 包含插件目录外文件或额外 DLL。')
            if not 0 < entry.file_size <= MAX_DLL_BYTES:
                raise ValueError('Mod Settings DLL 大小无效。')
            # Validate local names/headers of sidecars as well; no files are extracted.
            for _, other in files:
                with package.open(other):
                    pass
            with package.open(entry) as stream:
                dll = stream.read(MAX_DLL_BYTES + 1)
            if len(dll) != entry.file_size:
                raise ValueError('Mod Settings DLL 大小不匹配。')
            return dll
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, struct.error, UnicodeError) as error:
        raise ValueError('Mod Settings ZIP 文件结构无效。') from error


def plugin_version(dll, reader):
    with tempfile.TemporaryDirectory(prefix='modsettingsmanager-metadata-') as folder:
        path = Path(folder) / 'OC2ModSettings.dll'
        path.write_bytes(dll)
        result = subprocess.run(['dotnet', str(reader), str(path)], capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
    if result.returncode != 0:
        raise ValueError('DLL BepInPlugin 元数据检查失败。')
    try:
        version = json.loads(result.stdout)['version']
    except (ValueError, KeyError, TypeError):
        raise ValueError('插件版本读取器返回的元数据无效。') from None
    numeric_version(version)
    return version


def collect_record(api, url, reader, release_note=None):
    link = parse_link(url)
    record = asset_metadata(api, link)
    body = download_package(link.url, record['size'])
    if len(body) != record['size'] or hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('下载的 ZIP 与 API 提供的大小或 SHA256 不一致。')
    version = plugin_version(plugin_dll(body), reader)
    # Read the actual plugin version; never infer it from the tag or AssemblyVersion.
    if numeric_version(link.tag[1:]) != numeric_version(version):
        raise ValueError('Release Tag 与 DLL BepInPlugin 版本的数值不一致。')
    if release_note and release_note.strip():
        record['changelog'] = release_note
    return {'tag':record['tag'], 'version':version, **{k:v for k,v in record.items() if k != 'tag'}}


def sync_indexes(api, record, dry_run=False):
    sync_public_indexes(api, record, dry_run, kind='modsettingsmanager')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reader', type=Path, default=ROOT/'artifacts/plugin-version-reader/PluginVersionReader.dll')
    parser.add_argument('--output', type=Path, help='将已核验的 Mod Settings 对象保存为 JSON。')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('GH_TOKEN')
    if not token:
        parser.error('请配置具有公开仓库内容权限的 GH_TOKEN。')
    try:
        api = Api(token, opener=urllib.request.build_opener(NoRedirect()))
        record = collect_record(api, os.environ.get('RELEASE_FILE_LINK'), args.reader,
                                release_note=os.environ.get('RELEASE_NOTE'))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'modsettingsmanager':record}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('已校验 Mod Settings BepInPlugin 版本：' + record['version'])
        sync_indexes(api, record, args.dry_run)
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(1, 'Mod Settings 同步失败（已完成的目标提交仍然有效）：' + message + '\n')


if __name__ == '__main__':
    main()
