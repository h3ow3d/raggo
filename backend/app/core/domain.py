"""Domain pack abstraction for pluggable datasets in raggo.

A DomainPack encapsulates all domain-specific configuration:
- database schema (tables, embeddings, indexes)
- seed data generation
- safe SQL tools
- intent classification rules
- prompt fragments
- display metadata

The core agent and API infrastructure consume a DomainPack at runtime,
allowing arbitrary datasets to plug in without modifying core code.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class FilterSpec:
    """Whitelisted filter columns for an embeddable resource.
    
    Maps user-facing filter keys to ORM column attribute names.
    Example: {"severity": "severity", "flight_id": "flight_id"}
    """
    columns: dict[str, str]


@dataclass(frozen=True)
class EmbeddableResource:
    """Configuration for a single embeddable table/resource in the domain.
    
    Attributes
    ----------
    name : str
        Identifier for this resource (e.g. "flight_logs", "ticket_messages").
    model : type
        SQLAlchemy ORM class representing the table.
    text_column : str
        Attribute name of the ORM model holding the text to embed.
    embedding_column : str
        Attribute name where the pgvector embedding is stored.
    embedding_model_column : str | None
        Provenance column for the embedding model name. May be None.
    embedding_dim_column : str | None
        Provenance column for the embedding dimension. May be None.
    embedded_at_column : str | None
        Timestamp column for when embedding occurred. May be None.
    filter_spec : FilterSpec
        Whitelisted filters for vector search.
    evidence_projection : Callable[[Any], dict]
        Projects an ORM row (possibly joined) into a dict with evidence
        fields suitable for the agent and API responses.
    joins : tuple
        Sequence of (joined_model, on_clause_factory) tuples. Each
        on_clause_factory is a callable(resource_model, joined_model) -> clause.
        Example: (Flight, lambda log, flt: flt.id == log.flight_id)
    """
    name: str
    model: type
    text_column: str
    embedding_column: str
    embedding_model_column: str | None
    embedding_dim_column: str | None
    embedded_at_column: str | None
    filter_spec: FilterSpec
    evidence_projection: Callable[[Any], dict]
    joins: tuple = ()


@dataclass(frozen=True)
class SafeSqlTool:
    """A safe, parameterised SQL query exposed to the agent.
    
    Attributes
    ----------
    name : str
        Tool identifier (e.g. "get_delayed_flights").
    func : Callable[..., list[dict]]
        Function signature: (session: Session, **validated_kwargs) -> list[dict].
        Must accept a SQLAlchemy session and return plain dicts.
    args_model : type[BaseModel]
        Pydantic model used to validate kwargs before passing to func.
        Raises ValidationError on invalid input.
    evidence_builder : Callable[[dict], dict]
        Converts a single result row dict into an evidence item for the agent.
    description : str
        Human-readable summary of what the tool does (for documentation).
    """
    name: str
    func: Callable[..., list[dict]]
    args_model: type  # Pydantic BaseModel
    evidence_builder: Callable[[dict], dict]
    description: str = ""


@dataclass(frozen=True)
class IntentRule:
    """A pattern-matching rule for intent classification.
    
    Attributes
    ----------
    matches : Callable[[str], bool]
        Returns True if this rule applies to the given question.
        Typically a keyword regex or callable wrapping one.
    plan : Callable[[str], IntentPlan]
        Builds an IntentPlan from the question when this rule matches.
    """
    matches: Callable[[str], bool]
    plan: Callable[[str], Any]  # returns IntentPlan from core.agent.intent


@dataclass(frozen=True)
class DisplayMetadata:
    """UI/display configuration for a domain.
    
    Attributes
    ----------
    domain_name : str
        Short identifier (e.g. "flights", "support_tickets").
    title : str
        Human-readable title for the UI (e.g. "rag-flight-lab", "rag-support-lab").
    record_label_singular : str
        Singular label for the primary ingested item (e.g. "flight log", "ticket").
    record_label_plural : str
        Plural label for the primary ingested items.
    dashboard_stats : list[dict]
        List of stat configs for the dashboard. Each dict:
        {
            "label": str,       # Display label
            "kind": str,        # "count", "count_embedded", etc.
            "resource": str,    # Resource/table name
        }
    form_schema : dict | None
        Optional JSON Schema for a "create item" form. Not used in MVP.
    """
    domain_name: str
    title: str
    record_label_singular: str
    record_label_plural: str
    dashboard_stats: list[dict]
    form_schema: dict | None = None
    version: str = "0.1.0"
    description: str = ""


@dataclass(frozen=True)
class DomainPack:
    """Complete domain configuration bundle.
    
    Attributes
    ----------
    name : str
        Domain identifier matching the module name (e.g. "flights").
    sqlalchemy_base : type
        The SQLAlchemy DeclarativeBase class for this domain's ORM models.
    init_sql_path : str
        Absolute filesystem path to the domain's init.sql file.
    embeddable_resources : tuple[EmbeddableResource, ...]
        All tables/resources in this domain that support embeddings and vector search.
    sql_tools : tuple[SafeSqlTool, ...]
        Safe SQL tools exposed to the agent.
    intent_rules : tuple[IntentRule, ...]
        Ordered intent classification rules. First match wins.
    seed : Callable[[Session], dict]
        Idempotent seeding function. Returns a dict with insert counts
        and a "skipped" boolean.
    has_existing_data : Callable[[Session], bool]
        Returns True if the domain's tables already have data.
    stats : Callable[[Session], dict]
        Returns a dict of counts for /stats endpoint.
    domain_context : str
        A short phrase describing the domain, used in the system prompt.
        Example: "flight operations" or "customer support operations".
    evidence_formatter : Callable[[list[dict]], str]
        Formats a list of evidence items into a prompt-ready string.
        The formatter defines citation tag conventions (e.g. [log:<id>]).
    display : DisplayMetadata
        UI-facing metadata for dashboards and forms.
    """
    name: str
    sqlalchemy_base: type
    init_sql_path: str
    embeddable_resources: tuple[EmbeddableResource, ...]
    sql_tools: tuple[SafeSqlTool, ...]
    intent_rules: tuple[IntentRule, ...]
    seed: Callable[[Any], dict]
    has_existing_data: Callable[[Any], bool]
    stats: Callable[[Any], dict]
    domain_context: str
    evidence_formatter: Callable[[list[dict]], str]
    display: DisplayMetadata
    # Optional domain-supplied default intent plan. When present, the
    # agent orchestrator uses it instead of the built-in vector-only
    # fallback. Receives the user question and returns an `IntentPlan`.
    default_intent_plan: Optional[Callable[[str], Any]] = None


def load_domain(name: str) -> DomainPack:
    """Load a domain pack by name.
    
    Parameters
    ----------
    name : str
        Domain name matching the module under app.domains.
        Example: "flights" loads app.domains.flights
    
    Returns
    -------
    DomainPack
        The loaded domain configuration.
    
    Raises
    ------
    ImportError
        If the domain module or its DOMAIN export does not exist.
    AttributeError
        If the domain module does not export a DOMAIN variable.
    """
    module = importlib.import_module(f"app.domains.{name}")
    return module.DOMAIN


__all__ = [
    "DomainPack",
    "EmbeddableResource",
    "FilterSpec",
    "SafeSqlTool",
    "IntentRule",
    "DisplayMetadata",
    "load_domain",
]
