"""
architecture_analyzer.py — Analyze codebase to understand app architecture layers.
Builds a map of components and their relationships for better blame analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent.code_indexer import registry


@dataclass
class ComponentInfo:
    name: str
    layer: str
    file: str
    repo: str
    dependencies: list[str] = field(default_factory=list)
    log_tags: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ArchitectureMap:
    components: list[ComponentInfo] = field(default_factory=list)
    layers: dict[str, list[str]] = field(default_factory=dict)
    data_flows: list[tuple[str, str]] = field(default_factory=list)  # (from, to)

    def summary_text(self) -> str:
        lines = ["## App Architecture Summary\n"]
        for layer, comps in self.layers.items():
            lines.append(f"### {layer}")
            for c in comps:
                lines.append(f"  - {c}")
        if self.data_flows:
            lines.append("\n### Data Flow Relationships")
            for frm, to in self.data_flows[:20]:
                lines.append(f"  - {frm} → {to}")
        return "\n".join(lines)


# Layer detection rules (order matters — first match wins)
LAYER_RULES: list[tuple[str, str, list[str]]] = [
    # (layer_name, file_path_hint, class_name_keywords)
    ("UI/Compose",      "ui|screen|compose|view|fragment|activity",
     ["Activity", "Fragment", "Screen", "Composable", "View"]),
    ("ViewModel",       "viewmodel|vm",
     ["ViewModel", "Presenter", "StateHolder"]),
    ("UseCase",         "usecase|interactor|domain",
     ["UseCase", "Interactor"]),
    ("Repository",      "repository|repo",
     ["Repository", "Repo"]),
    ("DataSource",      "datasource|source|remote|local|dao",
     ["DataSource", "Dao", "Api", "ApiService"]),
    ("Service/Manager", "service|manager|provider|helper|util|client",
     ["Service", "Manager", "Provider", "Client", "Helper"]),
    ("HAL/VHAL",        "hal|vhal|car|aidl|hidl",
     ["CarService", "VehicleHal", "HalClient"]),
    ("Model/Entity",    "model|entity|dto|data",
     ["Model", "Entity", "Data", "Response", "Request"]),
]


def detect_layer(file_path: str, class_name: str) -> str:
    fp = file_path.lower().replace("\\", "/")
    cn = class_name.lower()
    for layer, path_hint, class_hints in LAYER_RULES:
        if any(h in fp for h in path_hint.split("|")):
            return layer
        if any(h.lower() in cn for h in class_hints):
            return layer
    return "Unknown"


CLASS_RE = re.compile(r"(?:class|interface|object)\s+(\w+)")
INJECT_RE = re.compile(r"@(?:Inject|HiltViewModel|Singleton|ViewModelScoped)")
DEPENDENCY_RE = re.compile(r"(?:private\s+val|private\s+var|val|var)\s+\w+\s*:\s*(\w+)")


def analyze_repos() -> ArchitectureMap:
    arch = ArchitectureMap()
    layers: dict[str, list[str]] = {}

    for idx in registry.all_indexes():
        for chunk in idx.chunks:
            file = chunk["file"]
            text = chunk["text"]

            # Find class names
            for m in CLASS_RE.finditer(text):
                class_name = m.group(1)
                layer = detect_layer(file, class_name)

                comp = ComponentInfo(
                    name=class_name,
                    layer=layer,
                    file=file,
                    repo=chunk.get("repo", ""),
                )

                # Log tags from index
                for tag, files in idx.log_tag_map.items():
                    if file in files:
                        comp.log_tags.append(tag)

                # Dependencies
                for dm in DEPENDENCY_RE.finditer(text):
                    dep = dm.group(1)
                    if dep[0].isupper() and dep not in ("String", "Int", "Boolean", "List", "Map"):
                        comp.dependencies.append(dep)

                arch.components.append(comp)
                if layer not in layers:
                    layers[layer] = []
                if class_name not in layers[layer]:
                    layers[layer].append(class_name)

    arch.layers = layers

    # Build simple data flow from Repository → Service patterns
    repo_names = set(layers.get("Repository", []))
    service_names = set(layers.get("Service/Manager", []) + layers.get("DataSource", []))
    vm_names = set(layers.get("ViewModel", []))
    ui_names = set(layers.get("UI/Compose", []))

    for comp in arch.components:
        for dep in comp.dependencies:
            if comp.name in vm_names and dep in repo_names:
                arch.data_flows.append((comp.name, dep))
            elif comp.name in repo_names and dep in service_names:
                arch.data_flows.append((comp.name, dep))
            elif comp.name in ui_names and dep in vm_names:
                arch.data_flows.append((comp.name, dep))

    return arch


def build_blame_context(arch: ArchitectureMap, tags_in_log: list[str]) -> str:
    """
    Given log tags observed in a log file, build a focused blame context
    explaining which components were involved and their relationships.
    """
    tag_set = {t.lower() for t in tags_in_log}
    involved: list[ComponentInfo] = []

    for comp in arch.components:
        if (
            comp.name.lower() in tag_set
            or any(t.lower() in tag_set for t in comp.log_tags)
        ):
            involved.append(comp)

    if not involved:
        return arch.summary_text()

    lines = ["## Involved Components (from log tags)\n"]
    for comp in involved[:15]:
        lines.append(f"**{comp.name}** [{comp.layer}] — {comp.file}")
        if comp.dependencies:
            lines.append(f"  Dependencies: {', '.join(comp.dependencies[:5])}")
        if comp.log_tags:
            lines.append(f"  Log tags: {', '.join(comp.log_tags)}")

    lines.append("\n## Data Flows Involving These Components")
    for frm, to in arch.data_flows:
        if any(c.name in (frm, to) for c in involved):
            lines.append(f"  {frm} → {to}")

    return "\n".join(lines)


# Singleton cache
_arch_map: Optional[ArchitectureMap] = None


def get_arch_map(force_rebuild: bool = False) -> ArchitectureMap:
    global _arch_map
    if _arch_map is None or force_rebuild:
        _arch_map = analyze_repos()
    return _arch_map
