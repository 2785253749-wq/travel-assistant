from __future__ import annotations

import ast
from pathlib import Path

from app.locations.models import LocationCandidate, LocationQuery, LocationSearchResult
from app.locations.provider import LocationProvider


class FakeLocationProvider:
    def search(self, query: LocationQuery) -> LocationSearchResult:
        return LocationSearchResult(
            items=[
                LocationCandidate(
                    id="fake:location-1",
                    name=query.query,
                    latitude=24.4798,
                    longitude=118.0894,
                    city=query.city,
                    provider="fake",
                )
            ],
            provider="fake",
        )


def _search(provider: LocationProvider, query: LocationQuery) -> LocationSearchResult:
    return provider.search(query)


def test_fake_provider_satisfies_search_contract() -> None:
    result = _search(
        FakeLocationProvider(),
        LocationQuery(query="厦门大学", city="厦门"),
    )

    assert result.provider == "fake"
    assert result.items[0].id == "fake:location-1"
    assert result.items[0].city == "厦门"


def test_provider_module_imports_only_domain_contract_dependencies() -> None:
    source_path = Path(__import__("app.locations.provider").locations.provider.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "typing" in imported_modules
    assert "app.locations.models" in imported_modules
    assert not any(name.startswith("app.agent") for name in imported_modules)
    assert not any("baidu" in name.lower() for name in imported_modules)
    assert not any("providers.base" in name for name in imported_modules)
    assert not any(name.endswith("providers.places") for name in imported_modules)
