from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from ares import AresBot
from ares.behaviors.macro import (
    MacroPlan,
    AutoSupply,
    BuildWorkers,
    ProductionController,
    UpgradeController,
    SpawnController,
    ExpansionController,
    GasBuildingController,
    TechUp,
)


class MacroManager:
    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai

    @property
    def max_workers(self) -> int:
        return min(70, len(self.ai.townhalls) * 22)

    @property
    def required_upgrades(self) -> list[UpgradeId]:
        return [
            UpgradeId.ZERGMISSILEWEAPONSLEVEL1,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL2,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL3,
            UpgradeId.ZERGGROUNDARMORSLEVEL1,
            UpgradeId.ZERGGROUNDARMORSLEVEL2,
            UpgradeId.ZERGGROUNDARMORSLEVEL3,
            UpgradeId.OVERLORDSPEED,
        ]

    @property
    def upgrades_enabled(self) -> bool:
        return (self.ai.vespene > 95) or (
            self.ai.minerals > 500 and self.ai.vespene > 350
        )

    def update(self) -> None:
        if not self.ai.build_order_runner.build_completed:
            return
        macro_plan: MacroPlan = MacroPlan()
        macro_plan.add(AutoSupply(base_location=self.ai.start_location))
        macro_plan.add(BuildWorkers(to_count=70))
        if self.upgrades_enabled:
            macro_plan.add(
                UpgradeController(
                    upgrade_list=self.required_upgrades,
                    base_location=self.ai.start_location,
                )
            )
        if self.ai.vespene >= 100 and len(self.ai.mediator.get_own_army_dict[UnitID.QUEEN]) >= 6:
            macro_plan.add(
                TechUp(desired_tech=UnitID.LAIR, base_location=self.ai.start_location)
            )
        if self.ai.supply_workers > 60:
            macro_plan.add(
                TechUp(desired_tech=UnitID.HIVE, base_location=self.ai.start_location)
            )
        macro_plan.add(
            SpawnController(
                army_composition_dict={
                    UnitID.QUEEN: {"proportion": 1.0, "priority": 0}
                },
            )
        )
        if self.ai.supply_workers > 18:
            macro_plan.add(GasBuildingController(to_count=2 if self.ai.supply_workers > 40 else 1))
        max_pending: int = 2 if self.ai.minerals < 1250 else 4
        macro_plan.add(ExpansionController(to_count=99, max_pending=max_pending))

        self.ai.register_behavior(macro_plan)
