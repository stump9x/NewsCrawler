from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.intel.models import FeedSource

WATCHER_CSV_DEFAULT = (
    "https://raw.githubusercontent.com/thalesgroup-cert/Watcher/master/"
    "Watcher/Watcher/threats_watcher/datas/sources.csv"
)


class Command(BaseCommand):
    help = (
        "Seed FeedSource from rss_sources.json and/or Watcher's full sources.csv. "
        "Does not reactivate feeds that were auto-disabled after fetch errors."
    )

    def add_arguments(self, parser):
        parser.add_argument("--path", default="", help="Path to local JSON seed file")
        parser.add_argument(
            "--watcher-csv",
            nargs="?",
            const=WATCHER_CSV_DEFAULT,
            default="",
            help="Import Watcher sources.csv (URL or local path). "
            "Pass flag alone to use the official GitHub URL.",
        )
        parser.add_argument(
            "--max-confidence",
            type=int,
            default=5,
            help="Only import Watcher rows with confident <= N (default 5 = all)",
        )
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="Deactivate sources not present in the imported set",
        )
        parser.add_argument(
            "--force-activate",
            action="store_true",
            help="Force is_active=True even for previously disabled feeds",
        )

    def handle(self, *args, **options):
        rows: list[dict] = []
        seed_path = Path(options["path"]) if options["path"] else (
            Path(__file__).resolve().parents[3] / "workers" / "feeds" / "rss_sources.json"
        )
        if seed_path.exists():
            data = json.loads(seed_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows.extend(data)
                self.stdout.write(f"Loaded {len(data)} from {seed_path}")

        catalog_path = (
            Path(__file__).resolve().parents[3]
            / "workers"
            / "feeds"
            / "intel_catalog.json"
        )
        if catalog_path.exists():
            from apps.workers.feeds.intel_catalog import load_intel_catalog

            catalog = load_intel_catalog(catalog_path)
            for row in catalog:
                row = {**row, "from_intel_catalog": True}
                rows.append(row)
            self.stdout.write(f"Loaded {len(catalog)} from intel catalog")

        watcher = options["watcher_csv"]
        if watcher:
            rows.extend(self._load_watcher(watcher, max_conf=options["max_confidence"]))

        if not rows:
            self.stderr.write("No sources to seed (JSON missing and --watcher-csv not set)")
            return

        # Prefer later rows (intel catalog) when URLs collide.
        deduped: dict[str, dict] = {}
        for row in rows:
            url = (row.get("url") or "").strip()
            if url:
                deduped[url] = row
        rows = list(deduped.values())

        created = 0
        updated = 0
        urls: set[str] = set()
        for row in rows:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            urls.add(url)
            category = (row.get("category") or "news").lower()
            if category not in FeedSource.Category.values:
                category = FeedSource.Category.NEWS
            conf = int(row.get("confidence") or 2)
            conf = max(1, min(5, conf))
            fields = {
                "name": (row.get("name") or "rss")[:128],
                "category": category,
                "confidence": conf,
                "country": (row.get("country") or "")[:64],
                "country_code": (row.get("country_code") or "")[:8],
                "notes": (row.get("notes") or "")[:],
                "requires_tor": bool(row.get("requires_tor")),
            }
            existing = FeedSource.objects.filter(url=url).first()
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
                if options["force_activate"] or row.get("from_intel_catalog"):
                    existing.is_active = True
                existing.save()
                updated += 1
            else:
                FeedSource.objects.create(url=url, is_active=True, **fields)
                created += 1

        deactivated = 0
        if options["deactivate_missing"] and urls:
            deactivated = FeedSource.objects.exclude(url__in=urls).update(is_active=False)

        # The defense catalog used to contain homepage scrapers / hard-blocked
        # publishers. Remove those rows entirely so neither workers nor the
        # source UI can revive them.
        removed_non_rss = FeedSource.objects.filter(
            (Q(notes__icontains="transport=official-html")
             & ~Q(notes__icontains="official-html-curated")
             & ~Q(name="janes-defence-news"))
            | (Q(name__in=["japan-times", "focus-taiwan"])
               & ~Q(notes__icontains="official-html-curated"))
            | Q(
                url__in=(
                    "http://military.cnr.cn",
                    "http://military.cnr.cn/",
                    "https://military.cnr.cn",
                    "https://military.cnr.cn/",
                    "https://www.japantimes.co.jp/feed/",
                    "https://japantimes.co.jp/feed/",
                    # CNA Focus Taiwan: article links commonly return 403 to crawlers.
                    "https://feeds.feedburner.com/rsscna/engnews/",
                    "https://feeds.feedburner.com/rsscna/engnews",
                )
            )
        ).delete()[0]

        # Legacy cyber-breach/leak/ransomware feeds do not belong in
        # this defense-news project. Delete them from this DB only.
        removed_legacy_cyber = FeedSource.objects.filter(
            Q(category__in=["breach", "ransomware", "cert", "defacement"])
            | Q(notes__icontains="claim/dark-web")
            | Q(notes__icontains="cybersecurity news")
            | Q(notes__icontains="cyber risk research")
            | Q(
                name__in=[
                    "bitsight-blog",
                    "darkwebinformer",
                    "databreaches-net",
                    "therecord-media",
                    "bleepingcomputer",
                    "www-hackread-com",
                    "thehackernews",
                    "securityweek",
                    "undercodenews",
                    "hibp-breaches",
                    "ransomware-live-rss",
                    "cert-fr",
                    "us-cert",
                    "acsc",
                    "cisa-advisories",
                    "acsc-news",
                    "cert-pl",
                    "ncsc-uk",
                ]
            )
            | Q(
                url__in=[
                    "https://www.bitsight.com/blog/rss.xml",
                    "https://darkwebinformer.com/rss/",
                    "https://www.databreaches.net/feed/",
                    "https://databreaches.net/feed/",
                    "https://therecord.media/feed/",
                    "https://therecord.media/feed",
                    "https://www.bleepingcomputer.com/feed/",
                    "https://www.hackread.com/feed/",
                    "https://hackread.com/feed/",
                    "https://feeds.feedburner.com/TheHackersNews",
                    "https://www.securityweek.com/feed/",
                    "https://undercodenews.com/feed/",
                    "https://haveibeenpwned.com/Feed/TitleAndBreaches",
                    "https://www.ransomware.live/rss.xml",
                    "https://www.cert.ssi.gouv.fr/feed/",
                    "https://www.cisa.gov/cybersecurity-advisories/all.xml",
                    "https://www.cyber.gov.au/rss/news",
                    "https://cert.pl/en/rss.xml",
                    "https://www.ncsc.gov.uk/api/1/services/v1/report-rss-feed.xml",
                    "https://breach.news/feed",
                    "https://data.breach.news/feed",
                ]
            )
        ).delete()[0]

        from apps.workers.feeds.forum_safety import DIRECT_FORUM_FEED_NAMES

        direct_forum_off = FeedSource.objects.filter(
            name__in=sorted(DIRECT_FORUM_FEED_NAMES)
        ).delete()[0]

        # Drop inactive leftovers so old project sources cannot linger in the UI.
        pruned_inactive = 0
        if options["deactivate_missing"] and urls:
            pruned_inactive = FeedSource.objects.filter(is_active=False).exclude(
                url__in=urls
            ).delete()[0]

        purged_wire = self._purge_off_catalog_wire(urls)

        self.stdout.write(
            self.style.SUCCESS(
                f"Feed sources seed complete · created={created} updated={updated} "
                f"deactivated={deactivated} direct_forum_off={direct_forum_off} "
                f"removed_legacy_cyber={removed_legacy_cyber} "
                f"removed_non_rss={removed_non_rss} "
                f"pruned_inactive={pruned_inactive} "
                f"purged_off_catalog_wire={purged_wire} "
                f"total={FeedSource.objects.count()} "
                f"active={FeedSource.objects.filter(is_active=True).count()}"
            )
        )

    def _purge_off_catalog_wire(self, catalog_urls: set[str]) -> int:
        """Remove Wire rows whose publisher host is outside the active defense catalog."""
        from apps.intel.models import Threat

        allowed_hosts: set[str] = set()
        for url in catalog_urls:
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                allowed_hosts.add(host)
        # Known alias/redirect hosts for official defense feeds.
        allowed_hosts.update({"war.gov", "media.defense.gov"})
        if not allowed_hosts:
            return 0

        def _allowed(host: str) -> bool:
            host = (host or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if not host:
                return False
            if host in allowed_hosts:
                return True
            return any(host.endswith(f".{allowed}") for allowed in allowed_hosts)

        drop_ids: list[int] = []
        for threat in Threat.objects.only("id", "source_url").iterator():
            host = (urlparse(threat.source_url or "").hostname or "").lower()
            if not _allowed(host):
                drop_ids.append(threat.id)
        if not drop_ids:
            return 0
        return Threat.objects.filter(id__in=drop_ids).delete()[0]

    def _load_watcher(self, source: str, *, max_conf: int) -> list[dict]:
        max_conf = max(1, min(5, int(max_conf)))
        if source.startswith("http://") or source.startswith("https://"):
            raw = urllib.request.urlopen(source, timeout=60).read().decode("utf-8")
        else:
            raw = Path(source).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(raw), delimiter=";")
        out: list[dict] = []
        for row in reader:
            conf = int(row.get("confident") or 9)
            if conf > max_conf:
                continue
            url = (row.get("url") or "").strip()
            if not url:
                continue
            host = urlparse(url).hostname or "source"
            name = re.sub(r"[^a-z0-9-]+", "-", host.lower()).strip("-")[:48]
            out.append(
                {
                    "name": name,
                    "url": url,
                    "country": row.get("country") or "",
                    "country_code": row.get("country_code") or "",
                    "confidence": max(1, min(5, conf)),
                    "category": "news",
                    "notes": f"watcher confidence={conf}",
                }
            )
        self.stdout.write(f"Loaded {len(out)} from Watcher CSV (max_confidence={max_conf})")
        return out
