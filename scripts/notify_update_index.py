"""Notify the private index writer after a verified resource release, without writing indexes here."""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from resource_release import load_descriptor


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configuration(repository, token):
    if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not token:
        raise RuntimeError('Configure UPDATE_INDEX_REPOSITORY and UPDATE_INDEX_TOKEN before publishing.')


def notify(tag, repository, token, mode='resources-published', declaration=None):
    configuration(repository, token)
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag or ''):
        raise RuntimeError('Invalid resource tag.')
    if mode not in ('resources-published', 'resources-sync'):
        raise RuntimeError('Invalid resource notification mode.')
    payload = {'tag':tag}
    if declaration is not None:
        payload['declaration'] = declaration
    request = urllib.request.Request('https://api.github.com/repos/'+repository+'/dispatches', method='POST',
        data=json.dumps({'event_type':mode,'client_payload':payload}).encode(),
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                 'Content-Type':'application/json','User-Agent':'OC2-Resources-Index-Notification'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
            if response.status != 204:
                raise RuntimeError('Notification was not accepted.')
    except Exception:
        raise RuntimeError('Index notification failed. The resource release remains published; retry only the notification or index workflow, never republish its tag.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    parser.add_argument('--sync-current', action='store_true', help='Synchronize the current declaration tag without publishing a package.')
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    repository=os.environ.get('UPDATE_INDEX_REPOSITORY','')
    token=os.environ.get('UPDATE_INDEX_TOKEN','')
    try:
        configuration(repository, token)
        if not args.check_config:
            if args.sync_current:
                if args.tag:
                    raise RuntimeError('Use --sync-current without --tag; it reads current-resource-info.json.')
                declaration = load_descriptor(Path('current-resource-info.json'))
                notify(declaration['tag'], repository, token, 'resources-sync', declaration)
            else:
                notify(args.tag, repository, token)
        print('Index notification configuration valid.' if args.check_config else 'Index notification accepted; the private writer will validate release assets and merge resources into existing channel indexes; missing update.json files are skipped.')
    except RuntimeError as error:
        parser.exit(1,str(error)+'\n')


if __name__ == '__main__':
    main()
