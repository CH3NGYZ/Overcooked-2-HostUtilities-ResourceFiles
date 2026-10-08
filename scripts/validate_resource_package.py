"""Validate a resource package without requiring the local development test suite."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from resource_release import validate_changelogs

PACKAGE = 'HostUtilities.Resources.zip'
MANIFEST = 'HostUtilities.Resources.manifest'
INFO = 'HostUtilities.Resources.info.json'
METADATA = 'release-metadata.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(directory, repository_root):
    resource_root = repository_root / 'Resources'
    require(resource_root.is_dir(), 'Missing Resources directory.')
    metadata = json.loads((directory / METADATA).read_text(encoding='utf-8'))
    declaration = json.loads((repository_root / 'current-resource-info.json').read_text(encoding='utf-8-sig'))
    tag = metadata.get('tag', '')
    require(isinstance(tag, str) and re.fullmatch(r'v\d+\.\d+\.\d+', tag), 'Invalid package tag.')
    require(metadata.get('version') == tag.removeprefix('v'), 'Version must be derived from tag.')
    require(re.fullmatch(r'[a-f0-9]{40}', metadata.get('sourceCommit', '')), 'Invalid source commit.')
    for key in ('tag', 'minModVersion', 'resourceFormat', 'changelog', 'changelogs'):
        require(metadata.get(key) == declaration.get(key), 'Declaration differs from metadata: ' + key)
    require('version' not in declaration, 'Do not declare version separately from tag.')
    require('versionCode' not in declaration and 'versionCode' not in metadata, 'Resource versions must not declare versionCode.')
    validate_changelogs(metadata)
    package = directory / PACKAGE
    manifest = (directory / MANIFEST).read_bytes()
    require(digest(package) == metadata.get('sha256'), 'Package SHA256 mismatch.')
    require(package.stat().st_size == metadata.get('size'), 'Package size mismatch.')
    require(hashlib.sha256(manifest).hexdigest() == metadata.get('manifestSha256'), 'Manifest SHA256 mismatch.')
    require(not manifest.startswith(b'\xef\xbb\xbf') and b'\r' not in manifest, 'Manifest must be UTF-8 without BOM and use LF.')

    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len({name.casefold() for name in names}) == len(names), 'Duplicate ZIP entry.')
        require(len(entries) < 65535, 'ZIP entry count requires Zip64.')
        for entry in entries:
            name = entry.filename.rstrip('/')
            require(name and '\\' not in name and ':' not in name and not re.search(r'[\r\n]', name), 'Invalid ZIP path: ' + entry.filename)
            require(all(part not in ('', '.', '..') for part in name.split('/')), 'Unsafe ZIP path: ' + entry.filename)
            require(entry.extract_version < 45 and entry.compress_type in (0, 8), 'Unsupported ZIP format: ' + entry.filename)
            if any(ord(character) > 127 for character in name):
                require(entry.flag_bits & 0x800, 'Missing UTF-8 ZIP path flag: ' + entry.filename)
        require(archive.read(MANIFEST) == manifest, 'Embedded and external manifests differ.')
        listed = {}
        for line in manifest.decode('utf-8').splitlines():
            sha, size, relative = line.split(' ', 2)
            require(re.fullmatch(r'[a-f0-9]{64}', sha) and size.isdigit(), 'Invalid manifest line.')
            require(relative != MANIFEST and relative not in listed, 'Invalid or duplicate manifest entry: ' + relative)
            data = archive.read(relative)
            require(len(data) == int(size) and hashlib.sha256(data).hexdigest() == sha, 'Resource hash or size mismatch: ' + relative)
            listed[relative] = sha
        files = {entry.filename for entry in entries if not entry.is_dir()}
        require(files == set(listed) | {MANIFEST}, 'Manifest does not cover every file.')
        info = json.loads(archive.read(INFO).decode('utf-8'))
        require(info.get('schemaVersion') == 1 and info.get('resourceId') == 'OC2HostUtilitiesResource', 'Invalid installed resource information.')
        require('versionCode' not in info, 'Installed resource information must not declare versionCode.')
        for key in ('version', 'sourceCommit', 'minModVersion', 'resourceFormat'):
            require(info.get(key) == metadata.get(key), 'Installed information differs from metadata: ' + key)

        source_files = {path.relative_to(resource_root).as_posix(): path for path in resource_root.rglob('*') if path.is_file()}
        require(files - {MANIFEST, INFO} == set(source_files), 'ZIP files must match all Resources files at the ZIP top level.')
        for relative, path in source_files.items():
            require(digest(path) == listed[relative], 'Source resource differs from package: ' + relative)
        source_directories = {path.relative_to(resource_root).as_posix() + '/' for path in resource_root.rglob('*') if path.is_dir()}
        require({entry.filename for entry in entries if entry.is_dir()} == source_directories, 'ZIP directories must match Resources, including empty directories.')
    return len(source_files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('artifacts/resources'))
    parser.add_argument('--repository-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count = validate(args.directory.resolve(), args.repository_root.resolve())
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        parser.exit(1, 'Resource validation failed: ' + str(error) + '\n')
    print('Validated ' + str(count) + ' source files, ZIP layout, UTF-8 paths, manifest and release metadata.')


if __name__ == '__main__':
    main()
