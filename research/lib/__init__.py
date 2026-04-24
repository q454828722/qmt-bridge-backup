"""Research-side data access helpers."""

from .research_client import (
    AkshareResearchSource,
    ComparisonReport,
    DataDomain,
    DomainPolicy,
    FinancialDataset,
    QMTResearchSource,
    ResearchClient,
    SnapshotBundle,
    SnapshotDiffArtifact,
    SnapshotDiffReport,
    SourceName,
    TabularDataset,
    TushareResearchSource,
    diff_snapshots,
    load_snapshot,
    write_diff_report,
)

__all__ = [
    "AkshareResearchSource",
    "ComparisonReport",
    "DataDomain",
    "DomainPolicy",
    "FinancialDataset",
    "QMTResearchSource",
    "ResearchClient",
    "SnapshotBundle",
    "SnapshotDiffArtifact",
    "SnapshotDiffReport",
    "SourceName",
    "TabularDataset",
    "TushareResearchSource",
    "diff_snapshots",
    "load_snapshot",
    "write_diff_report",
]
