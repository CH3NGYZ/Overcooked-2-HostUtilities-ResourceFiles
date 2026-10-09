"""Notify the private index writer, without writing public indexes here."""
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


def dispatch(event, payload, repository, token):
    configuration(repository, token)
    request = urllib.request.Request('https://api.github.com/repos/'+repository+'/dispatches', method='POST',
        data=json.dumps({'event_type':event,'client_payload':payload}).encode(),
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                 'Content-Type':'application/json','User-Agent':'OC2-Resources-Index-Notification'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
            if response.status != 204:
                raise RuntimeError('Notification was not accepted.')
    except Exception:
        raise RuntimeError('Index notification failed; check the private writer configuration and retry the notification.') from None


def notify(tag, repository, token, mode='resources-published', declaration=None):
    configuration(repository, token)
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag or ''):
        raise RuntimeError('Invalid resource tag.')
    if mode not in ('resources-published', 'resources-sync'):
        raise RuntimeError('Invalid resource notification mode.')
    payload = {'tag':tag}
    if declaration is not None:
        payload['declaration'] = declaration
    try:
        dispatch(mode, payload, repository, token)
    except RuntimeError:
        raise RuntimeError('Index notification failed. The resource release remains published; retry only the notification or index workflow, never republish its tag.') from None


def notify_configurationmanager(url, repository, token):
    url = (url or '').strip()
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[^/?#\s]+/[^/?#\s]+\.zip', url):
        raise RuntimeError('Supply a GitHub Release ZIP file link.')
    dispatch('configurationmanager-sync', {'release_file_link':url}, repository, token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--sync-current', action='store_true', help='Synchronize the current declaration tag without publishing a package.')
    mode.add_argument('--configurationmanager', action='store_true', help='Synchronize the Release ZIP supplied in RELEASE_FILE_LINK.')
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    repository=os.environ.get('UPDATE_INDEX_REPOSITORY','')
    token=os.environ.get('UPDATE_INDEX_TOKEN','')
    try:
        configuration(repository, token)
        if not args.check_config:
            if args.configurationmanager:
                if args.tag:
                    raise RuntimeError('Use --configurationmanager without --tag; the tag is parsed from RELEASE_FILE_LINK.')
                notify_configurationmanager(os.environ.get('RELEASE_FILE_LINK',''), repository, token)
            elif args.sync_current:
                if args.tag:
                    raise RuntimeError('Use --sync-current without --tag; it reads current-resource-info.json.')
                declaration = load_descriptor(Path('current-resource-info.json'))
                notify(declaration['tag'], repository, token, 'resources-sync', declaration)
            else:
                notify(args.tag, repository, token)
        if args.check_config:
            print('Index notification configuration valid.')
        else:
            print('Index notification accepted; follow the private writer workflow for asset validation and merge results. Missing update.json files are skipped.')
    except RuntimeError as error:
        parser.exit(1,str(error)+'\n')


if __name__ == '__main__':
    main()
