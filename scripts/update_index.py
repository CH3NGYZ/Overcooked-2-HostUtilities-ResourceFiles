"""Verify one published resource release and update existing Stable/Beta indexes."""

import argparse
import base64
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from resource_release import validate_changelogs, validate_descriptor

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_REPO = "CH3NGYZ/Overcooked-2-HostUtilities-ResourceFiles"
REPOS = {c: "CH3NGYZ/Overcooked-2-HostUtilities-" + c.title() + "-Releases" for c in ("stable", "beta")}
RESOURCE_ASSETS = ("HostUtilities.Resources.zip", "HostUtilities.Resources.manifest", "release-metadata.json")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Api:
    def __init__(self, token, opener=None):
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None, missing=False):
        request = urllib.request.Request(
            "https://api.github.com/" + path,
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "OC2-Resources-Index",
            },
        )
        try:
            with self.opener.open(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and missing:
                return None
            raise


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
        raise ValueError("Invalid numeric version.")
    parts = tuple(map(int, value.split(".")))
    if any(p > 2147483647 for p in parts):
        raise ValueError("Version component exceeds Int32.")
    return parts + (0,) * (4 - len(parts))


def read_resource_source(api, source_ref=""):
    if source_ref and not re.fullmatch(r"[0-9a-fA-F]{40}", source_ref):
        raise ValueError("Supply a complete resource commit SHA, or leave it empty to read main.")
    ref = source_ref or "main"
    file = api.request("GET", f"repos/{RESOURCE_REPO}/contents/current-resource-info.json?ref={ref}", missing=True)
    if file is None:
        raise ValueError(f"Resource source {ref} has no current-resource-info.json; no fallback is used.")
    declared = validate_descriptor(json.loads(base64.b64decode(file["content"]).decode("utf-8-sig")))
    version(declared["version"])
    version(declared["minModVersion"])
    if declared["resourceFormat"] > 2147483647:
        raise ValueError("Resource format exceeds Int32.")
    return declared


def download(url, size):
    uri = urllib.parse.urlsplit(url)
    if uri.scheme != "https" or not uri.hostname or uri.username or uri.password:
        raise ValueError("Invalid public asset URL.")
    # Public downloads and their storage redirects must never receive the write token.
    request = urllib.request.Request(url, headers={"User-Agent": "OC2-Resources-Index"})
    with urllib.request.urlopen(request, timeout=1800) as response:
        body = response.read(size + 1)
    if len(body) != size:
        raise ValueError("Downloaded asset size differs from the official API.")
    return body


def verify_assets(release):
    assets, bodies = [], {}
    for name in RESOURCE_ASSETS:
        matches = [a for a in release["assets"] if a.get("name") == name and a.get("state") == "uploaded"]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate uploaded asset: " + name)
        asset = matches[0]
        size, digest = asset.get("size"), asset.get("digest")
        if type(size) is not int or size <= 0:
            raise ValueError("Invalid official asset size: " + name)
        valid_digest = isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
        if name == "release-metadata.json" or not valid_digest:
            expected_url = f"https://github.com/{RESOURCE_REPO}/releases/download/{release['tag_name']}/{name}"
            if asset.get("browser_download_url") != expected_url:
                raise ValueError("Official asset URL does not match the selected release: " + name)
            if name == "release-metadata.json" and size > 1024 * 1024:
                raise ValueError("Resource metadata exceeds 1 MiB.")
            body = download(expected_url, size)
            actual = "sha256:" + hashlib.sha256(body).hexdigest()
            if valid_digest and actual != digest.lower():
                raise ValueError("Downloaded asset digest differs from the official API: " + name)
            digest = actual
            bodies[name] = body
        if digest[7:] == "0" * 64:
            raise ValueError("Invalid zero asset digest: " + name)
        assets.append({"name": name, "size": size, "sha256": digest[7:].lower()})
    return assets, bodies


def publication_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("Invalid release publication time.")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def read_resource(api, declared):
    tag = declared["tag"]
    release = api.request("GET", f"repos/{RESOURCE_REPO}/releases/tags/{tag}", missing=True)
    if release is None:
        raise ValueError(f"Resource repository {RESOURCE_REPO} has no public Release for {tag}.")
    if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("The selected resource Release has the wrong tag, is a draft, or is a prerelease.")
    assets, bodies = verify_assets(release)
    metadata = json.loads(bodies["release-metadata.json"])
    if not isinstance(metadata, dict) or "versionCode" in metadata:
        raise ValueError("Invalid resource release metadata.")
    if type(metadata.get("resourceFormat")) is not int or any(
        metadata.get(k) != declared[k] for k in ("tag", "version", "minModVersion", "resourceFormat")
    ):
        raise ValueError("Resource declaration version/dependencies differ from the package; select its source or publish a new tag.")
    if (
        not isinstance(metadata.get("sourceCommit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", metadata["sourceCommit"])
        or metadata.get("size") != assets[0]["size"]
        or type(metadata.get("size")) is not int
        or not isinstance(metadata.get("sha256"), str)
        or metadata["sha256"].lower() != assets[0]["sha256"]
        or not isinstance(metadata.get("manifestSha256"), str)
        or metadata["manifestSha256"].lower() != assets[1]["sha256"]
    ):
        raise ValueError("Resource metadata differs from the official assets.")
    validate_changelogs(metadata)
    record = {k: copy.deepcopy(declared[k]) for k in ("version", "tag", "minModVersion", "resourceFormat", "changelog")}
    record.update(
        sourceCommit=metadata["sourceCommit"],
        assets=assets,
        publishedAt=publication_time(release.get("published_at")),
        changelogSha256=hashlib.sha256(declared["changelog"].encode()).hexdigest(),
    )
    if "changelogs" in declared:
        record["changelogs"] = copy.deepcopy(declared["changelogs"])
        record["changelogsSha256"] = hashlib.sha256(
            json.dumps(record["changelogs"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    return record


def merge_index(current, channel, record, kind="resources"):
    if kind not in ("resources", "configurationmanager", "auxiliary"):
        raise ValueError("Unsupported resource index section.")
    if current is None:
        return None
    if not isinstance(current, dict) or current.get("schemaVersion", 2) != 2 or current.get("channel", channel) != channel:
        raise ValueError("Invalid index structure or channel: " + channel)
    output = copy.deepcopy(current)
    if kind == "auxiliary":
        if not isinstance(record, dict) or not record or set(record) - {"proxies", "configurationmanager"}:
            raise ValueError("Auxiliary updates may contain only proxies and/or configurationmanager.")
        output.update(copy.deepcopy(record))
        return output
    if kind == "configurationmanager":
        output[kind] = copy.deepcopy(record)
        return output
    section = output.setdefault("resources", {})
    if not isinstance(section, dict) or not isinstance(section.get("releases", []), list):
        raise ValueError("Resource index must contain a resources.releases array.")
    rows, replaced = [], False
    for old in section.get("releases", []):
        if version(old["version"]) == version(record["version"]):
            if not replaced:
                rows.append(copy.deepcopy(record))
                replaced = True
        else:
            rows.append(old)
    if not replaced:
        rows.insert(0, copy.deepcopy(record))
    section["releases"] = rows[:5]
    return output


def read_index(api, channel, missing=True):
    file = api.request("GET", f"repos/{REPOS[channel]}/contents/update.json?ref=main", missing=missing)
    if file is None:
        return None, None
    try:
        current = json.loads(base64.b64decode(file["content"]).decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ValueError(channel + ": update.json is not valid UTF-8 JSON; repair it before synchronization.") from None
    if not isinstance(current, dict):
        raise ValueError(channel + ": update.json must contain an object; repair it before synchronization.")
    return file, current


def write_index(api, channel, record, dry_run=False, kind="resources"):
    path = f"repos/{REPOS[channel]}/contents/update.json"
    for attempt in range(5):
        file, current = read_index(api, channel)
        updated = merge_index(current, channel, record, kind)
        if updated is None:
            print(channel + ": update.json is missing; skipped without creating it.")
            return
        if updated == current:
            print(channel + ": index is unchanged.")
            return
        if dry_run:
            print(channel + ": proposed " + kind + " index verified (dry run).")
            return
        payload = {
            "message": "chore: refresh " + kind + " update index",
            "branch": "main",
            "sha": file["sha"],
            "content": base64.b64encode((json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
        }
        try:
            api.request("PUT", path, payload)
        except urllib.error.HTTPError as error:
            if error.code != 409 or attempt == 4:
                raise
            time.sleep(attempt + 1)
            continue
        _, saved = read_index(api, channel, missing=False)
        # The independent MOD writer may commit other sections after our successful PUT.
        owned = tuple(record) if kind == "auxiliary" else (kind,)
        if not isinstance(saved, dict) or any(saved.get(key) != updated[key] for key in owned):
            raise ValueError(channel + ": post-write verification failed for " + kind + ".")
        print(channel + ": " + kind + " updated and verified.")
        return


def sync_indexes(api, record, dry_run=False, kind="resources"):
    targets = []
    # Preflight both files before the first commit; this is not a cross-repository transaction.
    for channel in REPOS:
        _, current = read_index(api, channel)
        if merge_index(current, channel, record, kind) is None:
            print(channel + ": update.json is missing; skipped.")
        else:
            targets.append(channel)
    for channel in targets:
        write_index(api, channel, record, dry_run, kind)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="", help="Optional tag check; must match the selected declaration.")
    parser.add_argument("--resource-source-ref", default="", help="Complete resource commit SHA; default: remote main.")
    parser.add_argument("--dry-run", action="store_true", help="Verify real sources and proposed indexes without writing.")
    parser.add_argument("--check-config", action="store_true", help="Check that GH_TOKEN is configured; no network calls.")
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN")
    if not token:
        parser.error("Configure GH_TOKEN with Contents read/write access to both public release repositories.")
    try:
        if args.check_config:
            print("Index token is configured; its repository permissions have not been probed.")
            return
        api = Api(token)
        declared = read_resource_source(api, args.resource_source_ref)
        if args.tag and args.tag != declared["tag"]:
            raise ValueError(
                f"Requested tag {args.tag} does not match current-resource-info.json tag {declared['tag']} "
                f"at resource source {args.resource_source_ref or 'main'}."
            )
        record = read_resource(api, declared)
        print("Verified resource Release " + record["tag"] + "; declaration source: " + (args.resource_source_ref or "main") + ".")
        sync_indexes(api, record, args.dry_run)
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        parser.exit(1, "Resource index synchronization failed (previously verified target commits remain valid): " + message + "\n")


if __name__ == "__main__":
    main()
