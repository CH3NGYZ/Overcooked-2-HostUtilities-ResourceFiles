"""Manually synchronize proxies and/or ConfigurationManager in existing indexes."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import urllib.parse

from configurationmanager_index import collect_record
from update_index import Api, ROOT, sync_indexes


def validate_proxies(config):
    if config.get('schemaVersion') != 1 or not isinstance(config.get('proxies'), list):
        raise ValueError('代理目录结构无效。')
    ids = set()
    for entry in config['proxies']:
        if set(entry) != {'id', 'name', 'platform', 'purposes', 'template', 'enabled'}:
            raise ValueError('代理包含非预期字段。')
        if (not isinstance(entry['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', entry['id'])
            or entry['id'] in ids or entry['id'] in ('auto', 'official') or not isinstance(entry['name'], str)
            or not entry['name'] or entry['platform'] not in ('github', 'gitee')
            or type(entry['enabled']) is not bool or not isinstance(entry['purposes'], list)
            or not entry['purposes'] or len(set(entry['purposes'])) != len(entry['purposes'])
            or any(p not in ('raw', 'api', 'release') for p in entry['purposes'])):
            raise ValueError('代理条目无效。')
        template = entry['template']
        if not isinstance(template, str):
            raise ValueError('代理模板无效。')
        placeholders = re.findall(r'\{([^{}]+)\}', template)
        allowed = {'url', 'url_no_scheme', 'url_path'}
        if entry['platform'] == 'github' and entry['purposes'] == ['release']:
            allowed.add('github_path')
        repository = {'owner', 'username', 'repo', 'repository', 'branch', 'path', 'file_path'}
        if entry['platform'] == 'github' and entry['purposes'] == ['raw']:
            allowed |= repository
        if not placeholders or len(placeholders) != len(set(placeholders)) or set(placeholders) - allowed:
            raise ValueError('代理占位符未知或重复。')
        if set(placeholders) & repository:
            if (len(set(placeholders) & {'owner', 'username'}) != 1
                or len(set(placeholders) & {'repo', 'repository'}) != 1
                or len(set(placeholders) & {'path', 'file_path'}) != 1 or 'branch' not in placeholders
                or set(placeholders) - repository):
                raise ValueError('仓库模板不完整。')
        elif len(placeholders) != 1 or not template.endswith('{' + placeholders[0] + '}'):
            raise ValueError('URL 模板存在歧义。')
        prefix = template.split('{')[0]
        parsed = urllib.parse.urlsplit(prefix)
        stripped = re.sub(r'\{[^{}]+\}', 'sample', template)
        if ('{' in stripped or '}' in stripped or any(c.isspace() for c in template)
            or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or not prefix.startswith('https://' + parsed.netloc + '/')
            or '#' in template or ('?' in template and not template.endswith('?{url}'))):
            raise ValueError('HTTPS 代理模板无效。')
        ids.add(entry['id'])
    return copy.deepcopy(config['proxies'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('proxies', 'configurationmanager', 'both'), required=True)
    parser.add_argument('--reader', type=Path, default=ROOT/'artifacts/plugin-version-reader/PluginVersionReader.dll')
    parser.add_argument('--output', type=Path, help='Save the verified selected sections as JSON.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('GH_TOKEN')
    if not token:
        parser.error('Configure GH_TOKEN with Contents read/write access to the two public release repositories.')
    try:
        api = Api(token)
        updates = {}
        # Verify every selected source before reading or writing either target index.
        if args.target in ('proxies', 'both'):
            updates['proxies'] = validate_proxies(json.loads((ROOT/'proxies.json').read_text(encoding='utf-8-sig')))
        if args.target in ('configurationmanager', 'both'):
            updates['configurationmanager'] = collect_record(api, os.environ.get('RELEASE_FILE_LINK'), args.reader)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(updates, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Verified auxiliary sections: ' + ', '.join(updates) + '.')
        sync_indexes(api, updates, args.dry_run, kind='auxiliary')
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        parser.exit(1, 'Auxiliary index synchronization failed (previously verified target commits remain valid): ' + message + '\n')


if __name__ == '__main__':
    main()

