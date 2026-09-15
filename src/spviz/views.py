"""Named display configurations sharing a captured product's array."""

from __future__ import annotations

from numbers import Integral, Real

import numpy as np


def resolve_views(views, product):
    if not isinstance(views, dict) or not views:
        raise ValueError("views must be a non-empty dictionary of named configurations")
    primary = product.get("primary_view")
    if primary is not None and (not isinstance(primary, str) or primary not in views):
        raise ValueError("primary_view must name one of the configured views")
    primary = primary if primary is not None else next(iter(views))
    result = []
    allowed = {
        "representation",
        "scale",
        "vmin",
        "vmax",
        "units",
        "view_axes",
        "overview_aspect",
    }
    for name, options in views.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("view names must be non-empty strings")
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValueError(f"Invalid options for view {name!r}")
        view = {**product, **options}
        representation = view.get("representation", "auto")
        if representation not in {
            "auto",
            "real",
            "imag",
            "magnitude",
            "power",
            "phase",
        }:
            raise ValueError("Invalid view representation")
        if (
            representation in {"imag", "phase"}
            and np.dtype(product["dtype"]).kind != "c"
        ):
            raise ValueError(f"View {name!r} requires complex-valued data")
        # Display bounds and units belong to a representation; do not inherit
        # amplitude bounds into phase (or power) when changing representation.
        changed = representation != product.get("representation", "auto")
        for source, target in (("vmin", "display_min"), ("vmax", "display_max")):
            view[target] = options.get(source, None if changed else product.get(target))
            value = view[target]
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not np.isfinite(value)
            ):
                raise ValueError("View limits must be finite numbers")
        if representation == "phase":
            view["scale"] = options.get("scale", "linear")
            view["units"] = options.get("units", "rad")
            if view["scale"] != "linear":
                raise ValueError("Phase views require linear scale")
            if view["display_min"] is None:
                view["display_min"] = -float(np.pi)
            if view["display_max"] is None:
                view["display_max"] = float(np.pi)
        elif changed:
            view["units"] = options.get("units")
        if view.get("units") is not None and not isinstance(view["units"], str):
            raise ValueError("View units must be a string or None")
        if view.get("scale", "linear") not in {"linear", "log"}:
            raise ValueError("Invalid view scale")
        if view.get("overview_aspect") not in {None, "data", "equal", "fit"}:
            raise ValueError("Invalid view overview_aspect")
        lo, hi = view["display_min"], view["display_max"]
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError("View vmin must be less than vmax")
        if product.get("statistics") == "none" and (lo is None or hi is None):
            raise ValueError("statistics='none' requires view vmin and vmax")
        axes = product["axes"]
        requested = view["view_axes"]
        if not isinstance(requested, (list, tuple)) or not 1 <= len(requested) <= min(
            3, len(axes)
        ):
            raise ValueError("Invalid view_axes")
        resolved = []
        for axis in requested:
            if (
                isinstance(axis, Integral)
                and not isinstance(axis, bool)
                and -len(axes) <= axis < len(axes)
            ):
                axis = axes[axis]
            if not isinstance(axis, str) or axis not in axes or axis in resolved:
                raise ValueError("Invalid or duplicate view axis")
            resolved.append(axis)
        if len(axes) > 3 and len(resolved) != 3:
            raise ValueError(
                "Arrays with more than 3 dimensions require exactly 3 view_axes"
            )
        view["view_axes"] = resolved
        view["view_name"] = name
        view["primary_view"] = primary
        view["is_primary"] = name == primary
        result.append(view)
    return result


def expand_views(manifest):
    """Expose renderable views without changing on-disk capture identity."""
    expanded = []
    reserved = {p["id"] for p in manifest["products"]}
    for product in manifest["products"]:
        if "views" not in product:
            expanded.append(product)
            continue
        for index, view in enumerate(resolve_views(product["views"], product)):
            view.pop("views", None)
            view.pop("view_stats", None)
            view["capture_id"] = product["id"]
            view["capture_name"] = product["name"]
            view["name"] = f"{product['name']} · {view['view_name']}"
            if index:
                candidate = f"{product['id'][:96]}--view-{index + 1}"
                while candidate in reserved:
                    candidate += "-v"
                view["id"] = candidate
                reserved.add(candidate)
            view["stats"] = product["view_stats"][view["view_name"]]
            expanded.append(view)
    return {
        **manifest,
        "capture_count": len(manifest["products"]),
        "products": expanded,
    }
