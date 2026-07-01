"""The adapter registry.

:class:`AdapterRegistry` holds the registered :class:`~.base.SourceAdapter`
instances in a fixed, deterministic order that encodes the default
:data:`~.base.SOURCE_PRIORITY` ranking. Given a :class:`~.base.SourceRef`, it
resolves the single adapter that should handle the reference.

Determinism (Req 12): resolution never depends on dict iteration order or wall
clock. Adapters are stored in a list and, before resolution, sorted by
``(priority, source_type)`` so ties between adapters that both claim a reference
are broken in favour of the more authoritative source type, then lexically by
source type name. This makes ``resolve`` a pure function of the registered set
and the reference.

Extension point: concrete adapters (the CSV/JSON/resume/etc. adapters built in
tasks 5.2-5.5) register themselves via :meth:`AdapterRegistry.register` or are
passed to :meth:`AdapterRegistry.with_defaults`. The registry depends only on the
:class:`~.base.SourceAdapter` protocol, so any object satisfying that protocol can
be plugged in without modifying this module.
"""

from __future__ import annotations

from .base import SourceAdapter, SourceRef, priority_of

__all__ = ["AdapterRegistry", "NoAdapterFoundError"]


class NoAdapterFoundError(LookupError):
    """Raised by :meth:`AdapterRegistry.resolve` when no adapter can handle a ref."""


class AdapterRegistry:
    """A fixed-order collection of source adapters with deterministic resolution.

    The registration order encodes default SourcePriority; resolution is made fully
    deterministic by sorting candidates on ``(priority, source_type)`` regardless of
    the order in which they were registered.
    """

    def __init__(self, adapters: list[SourceAdapter] | None = None) -> None:
        self._adapters: list[SourceAdapter] = []
        for adapter in adapters or []:
            self.register(adapter)

    @property
    def adapters(self) -> list[SourceAdapter]:
        """The registered adapters in deterministic priority order (a copy)."""
        return list(self._adapters)

    def register(self, adapter: SourceAdapter) -> None:
        """Register ``adapter``, keeping the collection in deterministic order.

        The list is re-sorted by ``(priority, source_type)`` after each insertion so
        the stored order always reflects SourcePriority and is independent of the
        order adapters were added in.
        """
        self._adapters.append(adapter)
        self._adapters.sort(key=self._order_key)

    def resolve(self, ref: SourceRef) -> SourceAdapter:
        """Return the adapter that should handle ``ref``.

        The first adapter (in deterministic priority order) whose
        :meth:`~.base.SourceAdapter.can_handle` returns ``True`` wins. When an
        explicit ``ref.source_type`` hint is present, only adapters of that exact
        type are considered, so a caller can force a specific adapter.

        Raises :class:`NoAdapterFoundError` when no adapter recognizes the reference.
        """
        candidates = self._adapters
        if ref.source_type is not None:
            candidates = [a for a in candidates if a.source_type == ref.source_type]

        for adapter in candidates:
            if adapter.can_handle(ref):
                return adapter

        raise NoAdapterFoundError(
            f"No registered adapter can handle reference: location={ref.location!r}, "
            f"source_type={ref.source_type!r}"
        )

    @staticmethod
    def _order_key(adapter: SourceAdapter) -> tuple[int, str]:
        """Deterministic sort key: SourcePriority rank, then source type name.

        ``adapter.priority`` is used when set; otherwise the rank is derived from the
        adapter's ``source_type`` via :func:`~.base.priority_of`. The source type
        name is the final, total tie-breaker.
        """
        priority = getattr(adapter, "priority", None)
        if priority is None:
            priority = priority_of(adapter.source_type)
        return (priority, adapter.source_type)
