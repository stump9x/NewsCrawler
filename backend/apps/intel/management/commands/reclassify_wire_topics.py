"""Review existing Wire rows; hide off-topic articles without deleting history."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.core.models import WireFilterPrompt, WireFilterPromptRevision
from apps.core.wire_filter_policy import (
    DEFAULT_WIRE_FILTER_PROMPT, LEGACY_WIRE_FILTER_PROMPT,
    clear_wire_filter_prompt_cache, get_wire_filter_prompt_record,
)
from apps.core.wire_topics import TOPIC_LABELS, TOPIC_TAG_PREFIX, RETIRED_TOPIC_TAGS, classify_wire_topics
from apps.intel.models import Tag, Threat
from apps.intel.management.commands.purge_irrelevant_wire import threat_as_relevance_item
from apps.workers.services import is_wire_relevant
from apps.workers.geography import detect_geography_tag_slugs

MANAGED_TAGS = Q(slug__startswith=TOPIC_TAG_PREFIX) | Q(slug__startswith="geo-") | Q(slug="vietnam")


def backup_line(handle, data):
    handle.write(json.dumps(data, ensure_ascii=False) + "\n")
    handle.flush()


class Command(BaseCommand):
    help = "Preview or apply five-topic Wire classification, with a reversible JSONL backup."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--update-prompt", action="store_true")
        parser.add_argument("--backup", help="New JSONL path; required with --apply.")
        parser.add_argument("--restore", help="Restore only fields recorded in a previous backup.")
        parser.add_argument("--sample", type=int, default=10)

    def _sync_prompts(self, handle):
        admin = get_wire_filter_prompt_record()
        inherited = {admin.prompt, LEGACY_WIRE_FILTER_PROMPT}
        records = WireFilterPrompt.objects.filter(prompt__in=inherited)
        for policy in records:
            if policy.prompt == DEFAULT_WIRE_FILTER_PROMPT:
                continue
            with transaction.atomic():
                policy = WireFilterPrompt.objects.select_for_update().get(pk=policy.pk)
                if policy.prompt not in inherited:
                    continue
                backup_line(handle, {"kind": "policy", "id": policy.pk, "prompt": policy.prompt})
                self._revision(policy)
                policy.prompt = DEFAULT_WIRE_FILTER_PROMPT
                policy.save(update_fields=["prompt", "updated_at"])
                self._revision(policy)
        clear_wire_filter_prompt_cache()

    def _sync_topic_labels(self, handle):
        for tag in Tag.objects.filter(slug__in=[*TOPIC_LABELS, *RETIRED_TOPIC_TAGS]):
            retired = tag.slug in RETIRED_TOPIC_TAGS
            if not retired and tag.name == TOPIC_LABELS[tag.slug]:
                continue
            backup_line(handle, {
                "kind": "tag", "slug": tag.slug, "name": tag.name,
                "threat_ids": list(Threat.objects.filter(tags=tag).values_list("pk", flat=True)) if retired else [],
            })
            if not retired:
                tag.name = TOPIC_LABELS[tag.slug]
                tag.save(update_fields=["name"])

    @staticmethod
    def _revision(policy):
        WireFilterPromptRevision.objects.create(
            policy=policy, owner=policy.owner,
            owner_username=policy.owner.username if policy.owner else "",
            actor=None, action=WireFilterPromptRevision.Action.UPDATE,
            prompt=policy.prompt,
        )

    def _restore(self, path, apply):
        restored = 0
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row["kind"] not in {"article", "policy", "tag"}:
                    continue
                restored += 1
                if not apply:
                    continue
                with transaction.atomic():
                    if row["kind"] == "tag":
                        tag, _ = Tag.objects.update_or_create(slug=row["slug"], defaults={"name": row["name"]})
                        for obj in Threat.objects.filter(pk__in=row["threat_ids"]):
                            obj.tags.add(tag)
                    elif row["kind"] == "policy":
                        policy = WireFilterPrompt.objects.select_for_update().get(pk=row["id"])
                        # Preserve edits made by an administrator since rollout.
                        if policy.prompt != DEFAULT_WIRE_FILTER_PROMPT:
                            continue
                        self._revision(policy)
                        policy.prompt = row["prompt"]
                        policy.save(update_fields=["prompt", "updated_at"])
                        self._revision(policy)
                    else:
                        obj = Threat.objects.select_for_update().filter(pk=row["id"]).first()
                        if obj is None:
                            continue
                        payload = dict(obj.raw_payload or {})
                        if row["scope_present"]:
                            payload["wire_scope"] = row["wire_scope"]
                        else:
                            payload.pop("wire_scope", None)
                        obj.raw_payload = payload
                        obj.wire_relevant = row["wire_relevant"]
                        obj.save(update_fields=["raw_payload", "wire_relevant"])
                        obj.tags.remove(*obj.tags.filter(MANAGED_TAGS))
                        for slug in row["topic_tags"]:
                            tag, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": TOPIC_LABELS.get(slug, slug)})
                            obj.tags.add(tag)
        clear_wire_filter_prompt_cache()
        self.stdout.write(f"{'restored' if apply else 'would_restore'}={restored}")

    def handle(self, *args, **options):
        apply = options["apply"]
        if options["restore"]:
            return self._restore(options["restore"], apply)
        if apply and not options["backup"]:
            raise CommandError("--apply requires --backup; existing articles are never deleted.")
        path = Path(options["backup"]) if options["backup"] else None
        if apply and path.exists():
            raise CommandError("Backup already exists; choose a new path.")
        counts = Counter()
        topics = Counter()
        samples = []
        kept_samples = []
        with (path.open("x", encoding="utf-8") if apply else nullcontext(None)) as handle:
            if apply:
                self._sync_topic_labels(handle)
            if apply and options["update_prompt"]:
                self._sync_prompts(handle)
            # Personal favorites and documents remain intact. Limit to feed news.
            ids = Threat.objects.filter(source__in=[Threat.Source.NEWS, Threat.Source.X, Threat.Source.OSINT]).order_by("pk").values_list("pk", flat=True)
            for pk in ids.iterator(chunk_size=250):
                with transaction.atomic():
                    obj = Threat.objects.select_for_update().get(pk=pk) if apply else Threat.objects.get(pk=pk)
                    item = threat_as_relevance_item(obj)
                    match = classify_wire_topics(item)
                    relevant = is_wire_relevant(item, prompt=DEFAULT_WIRE_FILTER_PROMPT if options["update_prompt"] else None)
                    counts["scanned"] += 1
                    counts["keep" if relevant else "hide"] += 1
                    if relevant:
                        topics.update(match.codes)
                        if len(kept_samples) < max(0, options["sample"]):
                            kept_samples.append({"id": obj.pk, "title": obj.title, "topics": match.codes})
                    elif len(samples) < max(0, options["sample"]):
                        samples.append({"id": obj.pk, "title": obj.title, "reason": match.reason if not match.relevant else "Bị loại bởi chỉ dẫn hoặc rào chắn nội dung"})
                    if not apply:
                        continue
                    payload = dict(obj.raw_payload or {})
                    old_tags = list(obj.tags.filter(MANAGED_TAGS).values_list("slug", flat=True))
                    new_tags = (set(match.tags) | set(detect_geography_tag_slugs(*match.evidence))) if relevant else set()
                    scope = {**match.as_payload(), "accepted": relevant}
                    if obj.wire_relevant == relevant and payload.get("wire_scope") == scope and set(old_tags) == new_tags:
                        continue
                    backup_line(handle, {"kind": "article", "id": obj.pk,
                        "wire_relevant": obj.wire_relevant,
                        "scope_present": "wire_scope" in payload,
                        "wire_scope": payload.get("wire_scope"), "topic_tags": old_tags})
                    payload["wire_scope"] = scope
                    obj.raw_payload, obj.wire_relevant = payload, relevant
                    obj.save(update_fields=["raw_payload", "wire_relevant"])
                    obj.tags.remove(*obj.tags.filter(slug__in=set(old_tags) - new_tags))
                    for slug in new_tags:
                        tag, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": TOPIC_LABELS.get(slug, slug)})
                        obj.tags.add(tag)
                    counts["changed"] += 1
            if apply:
                # Remove retired choices even if they had no feed articles.
                Tag.objects.filter(slug__in=RETIRED_TOPIC_TAGS).delete()
        self.stdout.write(json.dumps({"apply": apply, **counts, "topics": topics, "kept_samples": kept_samples, "hidden_samples": samples}, ensure_ascii=False))
