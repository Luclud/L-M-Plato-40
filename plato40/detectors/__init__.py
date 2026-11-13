"""Detector registry. Each detector is one domain agent."""
from .data_quality import DataQualityAgent
from .energy import EnergyAgent
from .logistics import LogisticsAgent
from .s01_turnaround import TurnaroundAgent
from .s02_water import WaterAgent
from .s03_comfort import ComfortAgent
from .s04_maintenance import MaintenanceAgent
from .s05_surge import SurgeAgent
from .guest_service import GuestServiceAgent

REGISTRY = {cls.name: cls for cls in (
    DataQualityAgent, TurnaroundAgent, WaterAgent, ComfortAgent,
    MaintenanceAgent, SurgeAgent, EnergyAgent, LogisticsAgent, GuestServiceAgent,
)}


def build(names: list[str] | None = None):
    names = names or list(REGISTRY)
    return [REGISTRY[n]() for n in names]
