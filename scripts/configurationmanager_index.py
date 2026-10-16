"""校验 Release ZIP，并将单个 ConfigurationManager 对象合并到现有索引。"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
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
    if (not tag or any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in tag)
        or not name.lower().endswith('.zip') or '/' in name or '\\' in name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Release Tag 或 ZIP 文件名无效。')
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
    if official[1:] != link[1:]:
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
    request = urllib.request.Request(url, headers={'User-Agent':'OC2-ConfigurationManager-Index'})
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read(expected_size + 1)
    return body


def plugin_dll(body):
    with zipfile.ZipFile(io.BytesIO(body)) as package:
        matches = [entry for entry in package.infolist()
                   if entry.filename.replace('\\', '/').split('/')[-1].lower() == 'configurationmanager.dll']
        if len(matches) != 1:
            raise ValueError('ZIP 必须恰好包含一个 ConfigurationManager.dll。')
        entry = matches[0]
        if entry.is_dir() or not 0 < entry.file_size <= MAX_DLL_BYTES:
            raise ValueError('ConfigurationManager DLL 大小无效。')
        with package.open(entry) as stream:
            dll = stream.read(MAX_DLL_BYTES + 1)
        if len(dll) != entry.file_size:
            raise ValueError('ConfigurationManager DLL 大小不匹配。')
        return dll


def plugin_version(dll, reader):
    with tempfile.TemporaryDirectory(prefix='configurationmanager-metadata-') as folder:
        path = Path(folder) / 'ConfigurationManager.dll'
        path.write_bytes(dll)
        result = subprocess.run(['dotnet', str(reader), str(path)], capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
    if result.returncode != 0:
        raise ValueError('DLL BepInPlugin 元数据检查失败。')
    try:
        version = json.loads(result.stdout)['version']
    except (ValueError, KeyError, TypeError):
        raise ValueError('插件版本读取器返回的元数据无效。') from None
    if (not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+){0,2}', version)
        or any(int(p) > 2147483647 for p in version.split('.'))):
        raise ValueError('BepInPlugin 版本必须包含 2 到 4 个数值段。')
    return version


def collect_record(api, url, reader):
    link = parse_link(url)
    record = asset_metadata(api, link)
    body = download_package(link.url, record['size'])
    if len(body) != record['size'] or hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('下载的 ZIP 与 API 提供的大小或 SHA256 不一致。')
    version = plugin_version(plugin_dll(body), reader)
    # Tag and plugin version have independent sources. Never infer version from the tag or AssemblyVersion.
    return {'tag':record['tag'], 'version':version, **{k:v for k,v in record.items() if k != 'tag'}}


def sync_indexes(api, record, dry_run=False):
    sync_public_indexes(api, record, dry_run, kind='configurationmanager')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reader', type=Path, default=ROOT/'artifacts/plugin-version-reader/PluginVersionReader.dll')
    parser.add_argument('--output', type=Path, help='将已核验的 ConfigurationManager 对象保存为 JSON。')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('GH_TOKEN')
    if not token:
        parser.error('请配置具有公开仓库内容权限的 GH_TOKEN。')
    try:
        api = Api(token, opener=urllib.request.build_opener(NoRedirect()))
        record = collect_record(api, os.environ.get('RELEASE_FILE_LINK'), args.reader)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'configurationmanager':record}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('已校验 ConfigurationManager BepInPlugin 版本：' + record['version'])
        sync_indexes(api, record, args.dry_run)
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(1, 'ConfigurationManager 同步失败（已完成的目标提交仍然有效）：' + message + '\n')


if __name__ == '__main__':
    main()
