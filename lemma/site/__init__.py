"""Static, no-build preview for the lemmasub.net real-task surface.

This package renders the public task board and solved-proof explorer directly
from the Proof Atlas real-task artifacts (task bundles + solved ledger). The
output is plain HTML and CSS with no build step and no client-side data fetch,
matching the site's static deployment. It reads public data and writes public
pages; it performs no upload, commit, or chain write.
"""

from __future__ import annotations

from lemma.site.build import (
    SiteConfig,
    build_site,
    build_site_from_atlas,
    render_board,
    render_index,
    render_solved,
)

__all__ = [
    "SiteConfig",
    "build_site",
    "build_site_from_atlas",
    "render_board",
    "render_index",
    "render_solved",
]
