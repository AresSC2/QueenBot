from typing import TYPE_CHECKING, List, Optional, Set

from ares.consts import UnitRole
from cython_extensions import (
    cy_attack_ready,
    cy_closest_to,
    cy_distance_to,
    cy_distance_to_squared,
    cy_in_attack_range,
    cy_pick_enemy_target,
)
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit
from sc2.units import Units

from bot.consts import PROXY_STATIC_DEFENCE
from bot.managers.queen_bot_mediator import QueenBotMediator

if TYPE_CHECKING:
    from ares import AresBot

MELEE_TYPES: set[UnitID] = {UnitID.DRONE, UnitID.PROBE, UnitID.SCV, UnitID.ZERGLING}


class WorkerDefenceManager:
    queen_bot_mediator: QueenBotMediator

    def __init__(
        self,
        ai: "AresBot",
    ):
        self.ai: AresBot = ai

        self.bunker_drone_tags: Set[int] = set()
        self.worker_defence_tags: List[int] = []
        self.enemy_committed_worker_rush: bool = False

        self.cancelled_structures: bool = False

    def update(self) -> None:
        self._handle_worker_rush()
        self._handle_proxy_rush()

    def assign_drone_back_to_gathering(self, drone_tag: int) -> None:
        self.ai.mediator.assign_role(tag=drone_tag, role=UnitRole.GATHERING)

    def _handle_proxy_rush(self) -> None:
        bunkers: Units = self.ai.enemy_structures.filter(
            lambda s: s.type_id in PROXY_STATIC_DEFENCE
            and cy_distance_to(s.position, self.ai.start_location) < 60.0
        )
        marines: Units = self.ai.enemy_units.filter(
            lambda s: s.type_id == UnitID.MARINE
            and cy_distance_to(s.position, self.ai.start_location) < 60.0
        )
        scvs: Units = self.ai.enemy_units.filter(
            lambda s: s.type_id == UnitID.SCV
            and cy_distance_to(s.position, self.ai.start_location) < 60.0
        )

        if bunkers and len(self.bunker_drone_tags) < 9:
            if worker := self.ai.mediator.select_worker(
                target_position=self.ai.start_location
            ):
                self.bunker_drone_tags.add(worker.tag)
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.DEFENDING)

        if drones := self.ai.workers.tags_in(self.bunker_drone_tags):
            for drone in drones:
                if bunkers or marines or scvs:
                    if drone.health_percentage < 0.4:
                        drone.gather(
                            cy_closest_to(self.ai.start_location, self.ai.mineral_field)
                        )
                        self.bunker_drone_tags.remove(drone.tag)
                        self.assign_drone_back_to_gathering(drone.tag)
                    elif scvs:
                        drone.attack(cy_closest_to(drone.position, scvs))
                    elif marines:
                        cy_closest_to(drone.position, marines)
                        drone.attack(cy_closest_to(drone.position, marines))
                    else:
                        drone.attack(cy_closest_to(drone.position, bunkers))
                else:
                    drone.gather(
                        cy_closest_to(self.ai.start_location, self.ai.mineral_field)
                    )
                    self.bunker_drone_tags.remove(drone.tag)
                    self.assign_drone_back_to_gathering(drone.tag)

    def _handle_worker_rush(self) -> None:
        """zerglings too !"""

        def stack_detected(_enemy_workers: Units) -> bool:
            if (
                not _enemy_workers
                or _enemy_workers.amount <= 5
                or cy_distance_to_squared(_enemy_workers.center, self.ai.start_location)
                > 122
            ):
                return False
            return _enemy_workers.closer_than(0.5, _enemy_workers.center).amount > 5

        enemy_workers: Units = self.ai.enemy_units.filter(
            lambda u: u.type_id in MELEE_TYPES
            and (
                cy_distance_to(u.position, self.ai.start_location) < 25.0
                or cy_distance_to(u.position, self.ai.mediator.get_own_nat) < 4.0
            )
        )

        all_enemy_workers: Units = self.ai.enemy_units(MELEE_TYPES)
        enemy_lings: Units = enemy_workers(UnitID.ZERGLING)

        # this makes sure we go all in after defending
        if enemy_workers.amount > 8 and self.ai.time < 180:
            self.enemy_committed_worker_rush = True

        # cancel expansion, so we can build more drones
        if (
            self.enemy_committed_worker_rush
            and self.ai.time < 180
            and not self.cancelled_structures
        ):
            for structure in self.ai.structures({UnitID.HATCHERY, UnitID.SPAWNINGPOOL}):
                if structure.build_progress < 1.0:
                    self.ai.mediator.cancel_structure(structure=structure)
                    structure(AbilityId.CANCEL_BUILDINPROGRESS)
            self.cancelled_structures = True

        # calculate how many workers we should use to defend
        num_enemy_workers: int = enemy_workers.amount
        if num_enemy_workers > 0 and self.ai.workers:
            workers_needed: int = (
                num_enemy_workers
                if num_enemy_workers <= 6 and enemy_lings.amount <= 3
                else self.ai.workers.amount - 2
            )
            if len(self.worker_defence_tags) < workers_needed:
                workers_to_take: int = workers_needed - len(self.worker_defence_tags)
                if (
                    available_workers := self.ai.mediator.get_units_from_role(
                        role=UnitRole.GATHERING
                    )
                    .filter(
                        lambda u: not u.is_carrying_resource
                        and u.health_percentage > 0.5
                    )
                    .take(workers_to_take)
                ):
                    self.ai.mediator.batch_assign_role(
                        tags=available_workers.tags,
                        role=UnitRole.DEFENDING,
                    )
                    # remove from mining, otherwise can't assign new workers to min field
                    for worker in available_workers:
                        self.ai.mediator.remove_worker_from_mineral(
                            worker_tag=worker.tag
                        )
                        self.worker_defence_tags.append(worker.tag)

        # actually defend if there is a worker threat
        if (
            len(self.worker_defence_tags) > 0
            and self.ai.mineral_field
            and all_enemy_workers
        ):
            defence_workers: Units = self.ai.workers.tags_in(self.worker_defence_tags)
            close_mineral_patch: Unit = self.ai.mineral_field.closest_to(
                self.ai.start_location
            )
            if defence_workers and all_enemy_workers:
                for worker in defence_workers:
                    if worker.health_percentage < 0.3:
                        worker.gather(
                            cy_closest_to(self.ai.start_location, self.ai.mineral_field)
                        )
                        self.assign_drone_back_to_gathering(worker.tag)
                        continue

                    in_attack_range: list[Unit] = cy_in_attack_range(
                        worker, all_enemy_workers
                    )
                    in_range_target: Optional[Unit] = None
                    if in_attack_range:
                        in_range_target = cy_pick_enemy_target(in_attack_range)
                    closest_enemy: Unit = cy_closest_to(
                        worker.position, all_enemy_workers
                    )
                    if (
                        all_enemy_workers
                        and cy_distance_to(
                            cy_closest_to(
                                close_mineral_patch.position, all_enemy_workers
                            ).position,
                            close_mineral_patch.position,
                        )
                        < 2
                        and stack_detected(all_enemy_workers)
                    ):
                        worker.gather(close_mineral_patch)
                    # in attack range of enemy, prioritise attacking
                    elif in_range_target and cy_attack_ready(
                        self.ai, worker, in_range_target
                    ):
                        worker.attack(in_range_target)
                    # attack the workers
                    elif cy_attack_ready(self.ai, worker, closest_enemy):
                        worker.attack(closest_enemy)
                    else:
                        worker.gather(close_mineral_patch)
            # enemy worker rushed but they have no workers now, go for the kill
            elif (
                self.enemy_committed_worker_rush
                and defence_workers
                and self.ai.enemy_race == Race.Protoss
            ):
                for worker in defence_workers:
                    if worker.weapon_cooldown == 0:
                        worker.attack(self.ai.enemy_start_locations[0])
                    else:
                        worker.gather(close_mineral_patch)
            elif defence_workers:
                for worker in defence_workers:
                    self.assign_drone_back_to_gathering(worker.tag)
                    worker.gather(close_mineral_patch)
                self.worker_defence_tags = []
        elif len(self.worker_defence_tags) > 0:
            for tag in self.worker_defence_tags:
                self.assign_drone_back_to_gathering(tag)
            self.worker_defence_tags = []
