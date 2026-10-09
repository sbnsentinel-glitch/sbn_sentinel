from typing import Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class ResourceCapability:
    """Rich capability snapshot for a single FHIR resource type."""
    resource_type: str
    read: bool = False
    search_type: bool = False
    search_params: List[str] = field(default_factory=list)
    bulk_export: bool = False
    profile: str = None


class CapabilitySnapshot:
    """
    Parses a FHIR CapabilityStatement into a queryable capability model.
    Replaces the flat {resource: True/False} dict.
    """

    def __init__(self, resources: Dict[str, ResourceCapability] = None):
        self._resources: Dict[str, ResourceCapability] = resources or {}

    @classmethod
    def from_fhir_metadata(cls, data: Dict[str, Any]) -> "CapabilitySnapshot":
        """Parse a raw FHIR CapabilityStatement JSON into a CapabilitySnapshot."""
        resources: Dict[str, ResourceCapability] = {}

        for rest_entry in data.get("rest", []):
            for r in rest_entry.get("resource", []):
                rtype = r.get("type")
                if not rtype:
                    continue

                interactions = {i.get("code") for i in r.get("interaction", [])}
                search_params = [
                    sp.get("name") for sp in r.get("searchParam", []) if sp.get("name")
                ]
                profile = r.get("profile")

                resources[rtype] = ResourceCapability(
                    resource_type=rtype,
                    read="read" in interactions,
                    search_type="search-type" in interactions,
                    search_params=search_params,
                    bulk_export=False,  # determined separately from operation list
                    profile=profile,
                )

            # Check for $export operation support
            for op in rest_entry.get("operation", []):
                if op.get("name") == "export":
                    for rtype in resources:
                        resources[rtype].bulk_export = True

        return cls(resources=resources)

    @classmethod
    def mock(cls) -> "CapabilitySnapshot":
        """Returns a mock snapshot for tests."""
        return cls(resources={
            "Patient": ResourceCapability(
                resource_type="Patient",
                read=True,
                search_type=True,
                search_params=["_id", "_lastUpdated"],
                bulk_export=True,
            ),
            "Encounter": ResourceCapability(
                resource_type="Encounter",
                read=True,
                search_type=True,
                search_params=["_id", "_lastUpdated", "patient"],
            ),
            "Coverage": ResourceCapability(
                resource_type="Coverage",
                read=True,
                search_type=True,
                search_params=["_id", "patient"],
            ),
        })

    def supports(self, resource_type: str) -> bool:
        """Returns True if resource_type is supported for read or search."""
        cap = self._resources.get(resource_type)
        return cap is not None and (cap.read or cap.search_type)

    def supports_read(self, resource_type: str) -> bool:
        """Returns True if resource_type is readable."""
        cap = self._resources.get(resource_type)
        return cap is not None and cap.read

    def supports_search(self, resource_type: str) -> bool:
        """Returns True if resource_type is searchable."""
        cap = self._resources.get(resource_type)
        return cap is not None and cap.search_type

    def supports_last_updated(self, resource_type: str) -> bool:
        """Returns True if _lastUpdated is a supported search param."""
        cap = self._resources.get(resource_type)
        return cap is not None and "_lastUpdated" in cap.search_params

    def supports_bulk_export(self, resource_type: str) -> bool:
        """Returns True if bulk export is supported for the resource."""
        cap = self._resources.get(resource_type)
        return cap is not None and cap.bulk_export

    def get(self, resource_type: str) -> ResourceCapability:
        return self._resources.get(resource_type)

    def resource_types(self) -> List[str]:
        return list(self._resources.keys())
