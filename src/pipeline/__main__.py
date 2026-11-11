"""Command line interface.

    python -m pipeline fetch obis            # one source
    python -m pipeline fetch all             # every enabled source
    python -m pipeline clean gbif
    python -m pipeline unify                 # dedup + GeoParquet + DuckDB + summaries
    python -m pipeline docs                  # docs/SOURCES.md + docs/DATA_SUMMARY.md
    python -m pipeline run                   # fetch all -> clean all -> unify -> docs
    python -m pipeline status                # what is cached / built
    python -m pipeline analyze social        # analyses -> results/*.json
    python -m pipeline analyze showcase      # showcase aggregates -> showcase/results/showcase.json
    python -m pipeline viz                   # analyses -> figures/*.png

Every stage is idempotent: cached downloads are reused unless --force is given, and clean/unify
always rebuild their outputs from the stage before.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .config import load_config
from .http import DownloadInProgress, HttpClient
from .parallel import pmap

log = logging.getLogger("pipeline")


def _targets(which: str, allowed: list[str], cfg) -> list[str]:
    if which == "all":
        return [s for s in allowed if (cfg.source(s) or {}).get("enabled", True)]
    if which not in allowed:
        raise SystemExit(f"unknown source {which!r}; choose from: all, {', '.join(allowed)}")
    return [which]


def cmd_fetch(cfg, which: str, force: bool) -> int:
    from . import fetch

    def one(name: str) -> str | None:
        """Fetch one source; return its name if it failed."""
        http = HttpClient(cfg["http"])  # own session per source; the per-host throttle is shared
        t = time.time()
        log.info("== fetch %s", name)
        try:
            fetch.get(name).fetch(cfg, http, force=force, name=name)
            log.info("== fetch %s done in %.0fs", name, time.time() - t)
            return None
        except DownloadInProgress as e:  # another run has it; its finished files are reused later
            log.warning("fetch %s incomplete: %s", name, e)
            return name
        except Exception as e:  # stub, log and continue with the other sources
            log.exception("fetch %s FAILED: %s", name, e)
            return name

    # Sources live on different servers, so they are fetched side by side.
    workers = int(cfg["http"].get("parallel_sources", 1))
    failures = [n for n in pmap(one, _targets(which, fetch.ALL, cfg), workers) if n]
    if failures:
        log.error("failed sources: %s", ", ".join(failures))
    return 1 if failures and which != "all" else 0


def cmd_clean(cfg, which: str) -> int:
    from . import clean
    failures = []
    for name in _targets(which, clean.ALL, cfg):
        log.info("== clean %s", name)
        try:
            clean.get(name).clean(cfg, name=name)
        except clean.SourceUnavailable as e:
            log.warning("clean %s skipped: %s", name, e)
        except Exception as e:
            log.exception("clean %s FAILED: %s", name, e)
            failures.append(name)
    if failures:
        log.error("failed: %s", ", ".join(failures))
    return 1 if failures and which != "all" else 0


def cmd_unify(cfg) -> int:
    from .unify import build
    build.unify(cfg)
    return 0


def cmd_docs(cfg) -> int:
    from .unify import docs
    docs.write_all(cfg)
    return 0


def cmd_analyze(cfg, which: str) -> int:
    from .analysis import climate, showcase, social
    todo = {"social": social, "climate": climate, "showcase": showcase}
    for name in (todo if which == "all" else [which]):
        log.info("== analyze %s", name)
        todo[name].run(cfg)
    return 0


def cmd_status(cfg) -> int:
    from . import clean, fetch
    print(f"{'source':16s} {'raw files':>9s}  {'raw MB':>8s}  interim")
    for name in fetch.ALL:
        d = cfg.raw / {"sst": f"erddap_{cfg.source('sst').get('dataset_id')}",
                       "chlorophyll": f"erddap_{cfg.source('chlorophyll').get('dataset_id')}",
                       "sea_ice": "nsidc", "mpa": "wdpa"}.get(name, name)
        files = [p for p in d.rglob("*") if p.is_file()] if d.exists() else []
        mb = sum(p.stat().st_size for p in files) / 1e6
        built = clean.output_path(cfg, name) if name in clean.ALL else None
        print(f"{name:16s} {len(files):9d}  {mb:8.1f}  {'yes' if built and built.exists() else '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("source", nargs="?", default="all")
    f.add_argument("--force", action="store_true", help="re-download even if cached")
    c = sub.add_parser("clean")
    c.add_argument("source", nargs="?", default="all")
    sub.add_parser("unify")
    sub.add_parser("docs")
    r = sub.add_parser("run")
    r.add_argument("--force", action="store_true")
    sub.add_parser("status")
    an = sub.add_parser("analyze")
    an.add_argument("analysis", nargs="?", default="all", choices=["all", "social", "climate", "showcase"])
    sub.add_parser("viz")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "pyobis", "fiona", "pyogrio", "rasterio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config(a.config)

    if a.cmd == "fetch":
        return cmd_fetch(cfg, a.source, a.force)
    if a.cmd == "clean":
        return cmd_clean(cfg, a.source)
    if a.cmd == "unify":
        return cmd_unify(cfg)
    if a.cmd == "docs":
        return cmd_docs(cfg)
    if a.cmd == "status":
        return cmd_status(cfg)
    if a.cmd == "analyze":
        return cmd_analyze(cfg, a.analysis)
    if a.cmd == "viz":
        from . import viz
        viz.build(cfg)
        return 0
    if a.cmd == "run":
        rc = cmd_fetch(cfg, "all", a.force)
        rc |= cmd_clean(cfg, "all")
        rc |= cmd_unify(cfg)
        rc |= cmd_docs(cfg)
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())
