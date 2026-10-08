"""Validate resource declarations and request publication only when the tag changes."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def validate_changelogs(record):
    if not isinstance(record.get('changelog'), str):
        raise RuntimeError('Invalid fallback resource changelog.')
    if 'changelogs' in record:
        logs = record['changelogs']
        if not isinstance(logs, dict) or not logs or any(k not in ('zh-cn','en-us','ko-kr') or not isinstance(v,str) or not v.strip() for k,v in logs.items()):
            raise RuntimeError('Invalid localized resource changelogs.')


def load_descriptor(path):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    tag = config.get('tag', '')
    if (not isinstance(tag, str) or not re.fullmatch(r'v\d+\.\d+\.\d+', tag)
            or 'versionCode' in config
            or not isinstance(config.get('minModVersion'), str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', config['minModVersion'])
            or type(config.get('resourceFormat')) is not int or config['resourceFormat'] < 1
            or not isinstance(config.get('changelog'), str) or 'version' in config):
        raise RuntimeError('Invalid current-resource-info.json; version is derived from tag.')
    validate_changelogs(config)
    return dict(config, version=tag.removeprefix('v'))


def git(*args):
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8')


def detect(current, previous, event):
    tag=current['tag']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Invalid resource release tag.')
    return event == 'workflow_dispatch' or previous is None or tag != previous.get('tag')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event',choices=('push','workflow_dispatch'),required=True)
    parser.add_argument('--before',default='')
    parser.add_argument('--outputs',type=Path,required=True)
    args=parser.parse_args()
    current=load_descriptor(Path('current-resource-info.json'))
    previous=None
    if args.event=='push' and args.before and args.before!='0'*40:
        if not re.fullmatch(r'[a-fA-F0-9]{40}',args.before):
            raise ValueError('Invalid previous commit.')
        git('cat-file','-e',args.before+'^{commit}')
        # Read the former filename only from history; a rename alone must not publish.
        for name in ('current-resource-info.json', 'resource-release.json'):
            if git('ls-tree','--name-only',args.before,'--',name).strip():
                previous=json.loads(git('show',args.before+':'+name))
                break
    publish=str(detect(current,previous,args.event)).lower()
    with args.outputs.open('a',encoding='utf-8') as file:
        file.write('publish='+publish+'\n')
    print('Resource publication: '+publish+'; tag: '+current['tag'])


if __name__=='__main__':
    main()
