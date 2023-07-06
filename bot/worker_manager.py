"""
Speedmining based on sharpy:
https://github.com/DrInfy/sharpy-sc2/blob/404d32f55a3f8630fa298d1c6331fcc5b0628414/sharpy/plans/tactics/speed_mining.py#L78

Which is based on AiSee's Go implementation:
https://github.com/aiseeq/s2l/blob/main/lib/scl/workers.go

Thanks to both
"""


import math
from typing import Dict, List, Optional, Set

from bot.custom_bot_ai import CustomBotAI
from bot.manager import Manager
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

MINING_RADIUS: float = 1.35


class WorkerManager(Manager):
    def __init__(self, bot: CustomBotAI):
        super().__init__(bot)
        self.workers_per_gas: int = 3
        self.available_minerals: Units = Units([], self.bot)
        self.worker_to_mineral_patch_dict: Dict[int, int] = {}
        self.mineral_patch_to_list_of_workers: Dict[int, Set[int]] = {}

        self.worker_to_geyser_dict: Dict[int, int] = {}
        self.geyser_to_list_of_workers: Dict[int, Set[int]] = {}

        self.mineral_tag_to_mineral: Dict[int, Unit] = {}
        # store which townhall the worker is closest to
        self.worker_tag_to_townhall_tag: Dict[int, int] = {}
        # mineral targets are positions just before the mineral (for speed mining)
        self.mineral_target_dict: Dict[Point2, Point2] = dict()
        self._calculate_mineral_targets()

    async def update(self, iteration: int) -> None:
        workers: Units = self.bot.workers.filter(
            lambda u: u.tag not in self.bot.unselectable_worker_tags
        )
        if not workers:
            return

        self._assign_workers(workers)

        await self._collect_resources(workers)

        # self._print_debug_information()

    def select_worker(
        self, target: Point2, add_to_unselected: bool = True
    ) -> Optional[Unit]:
        workers: Units = self.bot.workers.filter(
            lambda w: w.tag in self.worker_to_mineral_patch_dict
            and not w.is_carrying_resource
            and w.health_percentage > 0.5
        )
        worker: Optional[Unit] = (
            workers.closest_to(target)
            if workers
            else (self.bot.workers.first if self.bot.workers else None)
        )
        if worker:
            if add_to_unselected:
                self.bot.unselectable_worker_tags.add(worker.tag)
            self.remove_worker_from_mineral(worker.tag)
        return worker

    async def _collect_resources(self, workers: Units) -> None:
        """
        Mineral and vespene collection, where each worker is assigned their own patch or gas building
        @param workers:
        @return:
        """
        gas_buildings: Dict[int, Unit] = {
            gas.tag: gas for gas in self.bot.gas_buildings
        }
        minerals: Dict[int, Unit] = {
            mineral.tag: mineral for mineral in self.bot.mineral_field
        }
        boost_active: bool = self.bot.supply_used < 198 and self.bot.minerals < 1000

        for worker in workers:
            worker_tag: int = worker.tag

            if worker_tag in self.worker_to_mineral_patch_dict:
                mineral_tag: int = self.worker_to_mineral_patch_dict[worker_tag]
                mineral: Optional[Unit] = minerals.get(mineral_tag, None)

                if mineral is None:
                    # Mined out or no vision? Remove it
                    self._remove_mineral_field(mineral_tag)

                elif boost_active:
                    await self._do_mining_boost(
                        1.08,
                        mineral,
                        worker,
                    )
                else:
                    if worker.is_carrying_vespene:
                        worker.return_resource()
                    elif not worker.is_carrying_minerals and (
                        not worker.is_gathering or worker.order_target != mineral.tag
                    ):
                        worker.gather(mineral)

            elif worker_tag in self.worker_to_geyser_dict:
                gas_building_tag: int = self.worker_to_geyser_dict[worker.tag]
                gas_building: Optional[Unit] = gas_buildings.get(gas_building_tag, None)
                if not gas_building or not gas_building.vespene_contents:
                    self._remove_gas_building(gas_building_tag)

                else:
                    townhalls: Units = self.bot.townhalls.filter(lambda th: th.is_ready)
                    if townhalls:
                        townhall: Unit = townhalls.closest_to(worker)
                        # can't use anything like `carry_resource`, `return_resource` for gas
                        # because we don't get the buff if worker is carrying rich vespene
                        if (
                            worker.order_target != gas_building.tag
                            and worker.order_target != townhall.tag
                        ):
                            worker.gather(gas_building)

            # nowhere for this worker to go, just mine anything
            elif self.bot.mineral_field:
                worker.gather(self.bot.mineral_field.closest_to(worker))

    async def _do_mining_boost(
        self,
        distance_to_townhall_factor: float,
        target: Unit,
        worker: Unit,
    ) -> None:
        """
        Perform the trick so that worker does not decelerate
        """
        if not self.bot.townhalls:
            return

        resource_target_pos: Point2 = self.mineral_target_dict.get(target.position)

        # shift worker to correct mineral if it ends up on wrong one
        if worker.is_gathering and worker.order_target != target.tag:
            worker(AbilityId.SMART, target)

        elif (worker.is_returning or worker.is_carrying_resource) and len(
            worker.orders
        ) < 2:
            townhall: Optional[Unit] = None
            if worker.tag in self.worker_tag_to_townhall_tag:
                if ths := self.bot.townhalls.filter(
                    lambda u: u.tag == self.worker_tag_to_townhall_tag[worker.tag]
                ):
                    townhall = ths.first
            if not townhall:
                townhall: Unit = self.bot.townhalls.closest_to(worker)

            target_pos: Point2 = townhall.position
            target_pos = target_pos.towards(
                worker, townhall.radius * distance_to_townhall_factor
            )
            if 0.75 < worker.distance_to(target_pos) < 2:
                worker.move(target_pos)
                worker(AbilityId.SMART, townhall, True)
            # not at right distance to get boost command, but doesn't have return resource command for some reason
            elif not worker.is_returning:
                worker(AbilityId.SMART, townhall)

        elif not worker.is_returning and len(worker.orders) < 2:
            min_distance: float = 0.75 if target.is_mineral_field else 0.1
            max_distance: float = 2.0 if target.is_mineral_field else 0.5
            if (
                min_distance < worker.distance_to(resource_target_pos) < max_distance
                or worker.is_idle
            ):
                worker.move(resource_target_pos)
                worker(AbilityId.SMART, target, True)

        # on rare occasion above conditions don't hit and worker goes idle
        elif worker.is_idle or not worker.is_moving:
            if worker.is_carrying_resource:
                worker.return_resource()
            else:
                worker.gather(target)

        # attempt to fix rare bug, worker sitting next to townhall with a resource
        elif not worker.is_returning and worker.is_carrying_resource:
            worker.return_resource()

    def _assign_workers(self, workers: Units) -> None:

        unassigned_workers: Units = self._get_unassigned_workers(workers)
        if not unassigned_workers:
            return
        # This takes priority
        if gb := self.bot.gas_buildings.ready:
            self._assign_worker_to_gas_buildings(gb, unassigned_workers)
        # got some workers to assign, refresh available minerals
        self.available_minerals = self._get_available_minerals()
        unassigned_workers: Units = self._get_unassigned_workers(workers)
        if self.available_minerals and unassigned_workers:
            self._assign_workers_to_mineral_patches(
                self.available_minerals, unassigned_workers
            )

    def _get_unassigned_workers(self, workers: Units) -> Units:
        return workers.filter(
            lambda u: u.tag not in self.worker_to_geyser_dict
            and u.tag not in self.worker_to_mineral_patch_dict
        )

    def _assign_worker_to_gas_buildings(
        self, gas_buildings: Units, unassigned_workers: Units
    ) -> None:
        """
        We only assign one worker per step, with the hope of grabbing drones on far mineral patches
        @param gas_buildings:
        @return:
        """
        if not self.bot.townhalls:
            return
        worker: Unit = unassigned_workers.first
        for gas in gas_buildings:
            # too many workers assigned, this can happen if we want to pull drones off gas
            if (
                len(self.geyser_to_list_of_workers.get(gas.tag, []))
                > self.workers_per_gas
            ):
                workers_on_gas: Units = self.bot.workers.tags_in(
                    self.geyser_to_list_of_workers[gas.tag]
                )
                if workers_on_gas:
                    self._remove_worker_from_vespene(workers_on_gas.first.tag)
                continue
            # already perfect amount of workers assigned
            if (
                len(self.geyser_to_list_of_workers.get(gas.tag, []))
                == self.workers_per_gas
            ):
                continue

            if not worker or worker.tag in self.geyser_to_list_of_workers:
                continue
            if (
                len(self.geyser_to_list_of_workers.get(gas.tag, []))
                < self.workers_per_gas
            ):
                if len(self.geyser_to_list_of_workers.get(gas.tag, [])) == 0:
                    self.geyser_to_list_of_workers[gas.tag] = {worker.tag}
                else:
                    if worker.tag not in self.geyser_to_list_of_workers[gas.tag]:
                        self.geyser_to_list_of_workers[gas.tag].add(worker.tag)
                self.worker_to_geyser_dict[worker.tag] = gas.tag
                self.worker_tag_to_townhall_tag[
                    worker.tag
                ] = self.bot.townhalls.closest_to(gas).tag

                break

    def _assign_workers_to_mineral_patches(
        self, available_minerals: Units, workers: Units
    ) -> None:
        """
        Given some minerals and workers, assign two to each mineral patch
        Thanks to burny's example worker stacking code:
        https://github.com/BurnySc2/python-sc2/blob/develop/examples/worker_stack_bot.py
        @param available_minerals:
        @param workers:
        @return:
        """
        if not self.bot.townhalls:
            return
        _minerals: Units = available_minerals
        if self.bot.time < 5:
            _minerals: Units = available_minerals.sorted_by_distance_to(
                self.bot.start_location
            )
        # equally disperse workers to far patches st the start of the game
        num_to_assign: int = (
            1 if self.bot.time < 5 and len(self.worker_to_mineral_patch_dict) > 8 else 2
        )

        for worker in workers:
            # run out of minerals to assign
            if not _minerals:
                return

            if worker.tag in self.worker_to_mineral_patch_dict:
                continue
            if worker.tag in self.worker_to_geyser_dict:
                continue

            if self.bot.time < 5:
                mineral: Unit = _minerals.closest_to(self.bot.start_location)
            else:
                mineral: Unit = _minerals.closest_to(worker)

            if (
                len(self.mineral_patch_to_list_of_workers.get(mineral.tag, []))
                < num_to_assign
            ):
                self._assign_worker_to_patch(mineral, worker)

            # enough have been assigned to this patch, don't consider it on next iteration over loop
            if (
                len(self.mineral_patch_to_list_of_workers.get(mineral.tag, []))
                >= num_to_assign
            ):
                _minerals.remove(mineral)

    def _assign_worker_to_patch(self, mineral_field: Unit, worker: Unit) -> None:
        mineral_tag: int = mineral_field.tag
        worker_tag: int = worker.tag
        if len(self.mineral_patch_to_list_of_workers.get(mineral_tag, [])) == 0:
            self.mineral_patch_to_list_of_workers[mineral_tag] = {worker_tag}
        else:
            if worker_tag not in self.mineral_patch_to_list_of_workers[mineral_tag]:
                self.mineral_patch_to_list_of_workers[mineral_tag].add(worker_tag)
        self.worker_to_mineral_patch_dict[worker_tag] = mineral_tag
        self.worker_tag_to_townhall_tag[worker_tag] = self.bot.townhalls.closest_to(
            mineral_field
        ).tag

    def _calculate_mineral_targets(self) -> None:

        for mf in self.bot.mineral_field:
            target: Point2 = mf.position
            center = target.closest(self.bot.expansion_locations_list)
            target = target.towards(center, MINING_RADIUS)
            close = self.bot.mineral_field.closer_than(MINING_RADIUS, target)
            for mf2 in close:
                if mf2.tag != mf.tag:
                    points = self._get_intersections(
                        mf.position.x,
                        mf.position.y,
                        MINING_RADIUS,
                        mf2.position.x,
                        mf2.position.y,
                        MINING_RADIUS,
                    )
                    if len(points) == 2:
                        target = center.closest(points)
            self.mineral_target_dict[mf.position] = target

    def _get_available_minerals(self) -> Units:
        """
        Find all mineral fields available near a townhall that don't have 2 workers assigned to it yet
        """
        available_minerals: Units = Units([], self.bot)
        progress: float = 0.9
        townhalls: Units = self.bot.townhalls.filter(
            lambda th: th.build_progress > progress
        )
        if not townhalls:
            return available_minerals

        for townhall in townhalls:
            if self.bot.mineral_field:
                # we want workers on closest mineral patch first
                minerals_sorted: Units = self.bot.mineral_field.filter(
                    lambda mf: mf.is_visible
                    and not mf.is_snapshot
                    and mf.distance_to(townhall) < 10
                    and len(self.mineral_patch_to_list_of_workers.get(mf.tag, [])) < 2
                ).sorted_by_distance_to(townhall)

                if minerals_sorted:
                    available_minerals.extend(minerals_sorted)

        return available_minerals

    def _remove_gas_building(self, gas_building_tag) -> None:
        """Remove gas building and assigned workers from bookkeeping"""
        if gas_building_tag in self.geyser_to_list_of_workers:
            del self.geyser_to_list_of_workers[gas_building_tag]
            self.worker_to_geyser_dict = {
                key: val
                for key, val in self.worker_to_geyser_dict.items()
                if val != gas_building_tag
            }

    def _remove_mineral_field(self, mineral_field_tag) -> None:
        """Remove mineral field and assigned workers from bookkeeping"""
        if mineral_field_tag in self.mineral_patch_to_list_of_workers:
            del self.mineral_patch_to_list_of_workers[mineral_field_tag]
            self.worker_to_mineral_patch_dict = {
                key: val
                for key, val in self.worker_to_mineral_patch_dict.items()
                if val != mineral_field_tag
            }

    def remove_worker(self, tag: int) -> None:
        self.remove_worker_from_mineral(tag)
        self._remove_worker_from_vespene(tag)

    def remove_worker_from_mineral(self, worker_tag: int) -> None:
        """
        Remove worker from internal data structures.
        This happens if worker gets assigned to do something else or is dead
        @param worker_tag:
        @return:
        """
        if worker_tag in self.worker_to_mineral_patch_dict:
            # found the worker, get the min tag before deleting
            min_patch_tag: int = self.worker_to_mineral_patch_dict[worker_tag]
            del self.worker_to_mineral_patch_dict[worker_tag]
            if worker_tag in self.worker_tag_to_townhall_tag:
                del self.worker_tag_to_townhall_tag[worker_tag]

            # using the min patch tag, we can remove from other collection
            self.mineral_patch_to_list_of_workers[min_patch_tag].remove(worker_tag)

    def _remove_worker_from_vespene(self, worker_tag: int) -> None:
        """
        Remove worker from internal data structures.
        This happens if worker gets assigned to do something else, or removing workers from gas
        @param worker_tag:
        @return:
        """
        if worker_tag in self.worker_to_geyser_dict:
            # found the worker, get the gas building tag before deleting
            gas_building_tag: int = self.worker_to_geyser_dict[worker_tag]
            del self.worker_to_geyser_dict[worker_tag]
            if worker_tag in self.worker_tag_to_townhall_tag:
                del self.worker_tag_to_townhall_tag[worker_tag]

            # using the gas building tag, we can remove from other collection
            self.geyser_to_list_of_workers[gas_building_tag].remove(worker_tag)

    @staticmethod
    def _get_intersections(
        x0: float, y0: float, r0: float, x1: float, y1: float, r1: float
    ) -> List[Point2]:
        d = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)

        # non intersecting
        if d > r0 + r1:
            return []
        # One circle within other
        if d < abs(r0 - r1):
            return []
        # coincident circles
        if d == 0 and r0 == r1:
            return []
        else:
            a = (r0**2 - r1**2 + d**2) / (2 * d)
            h = math.sqrt(r0**2 - a**2)
            x2 = x0 + a * (x1 - x0) / d
            y2 = y0 + a * (y1 - y0) / d
            x3 = x2 + h * (y1 - y0) / d
            y3 = y2 - h * (x1 - x0) / d

            x4 = x2 - h * (y1 - y0) / d
            y4 = y2 + h * (x1 - x0) / d

            return [Point2((x3, y3)), Point2((x4, y4))]

    def _print_debug_information(self) -> None:
        """

        @return:
        """
        # useful to tell which worker has been assigned where while debugging but can slow things
        for worker in self.bot.workers:
            self.bot.draw_text_on_world(worker.position, f"{worker.tag}")

        for mf in self.bot.mineral_field:
            self.bot.draw_text_on_world(mf.position, f"{mf.tag}")

        if self.bot.state.game_loop == 6720:
            print(
                f"{self.bot.time_formatted} Mined a total of {int(self.bot.minerals)} minerals"
            )

            print(
                f"{self.bot.time_formatted} Mined a total of {int(self.bot.state.score.collected_vespene)} vespene"
            )
