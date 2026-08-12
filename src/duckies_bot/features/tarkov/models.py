"""Normalized PvE Tarkov item data."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VendorPrice:
    vendor_name: str
    price_rub: int | None

    @property
    def is_flea_market(self) -> bool:
        return self.vendor_name.casefold() == "flea market"


@dataclass(frozen=True, slots=True)
class TarkovItem:
    id: str
    name: str
    short_name: str | None
    icon_url: str | None
    avg24h_price: int | None
    low24h_price: int | None
    high24h_price: int | None
    last_low_price: int | None
    sell_offers: tuple[VendorPrice, ...]
    task_names: tuple[str, ...]

    @property
    def best_trader_offer(self) -> VendorPrice | None:
        best_offer: VendorPrice | None = None
        best_price = -1
        for offer in self.sell_offers:
            if offer.price_rub is None or offer.is_flea_market:
                continue
            if offer.price_rub > best_price:
                best_offer = offer
                best_price = offer.price_rub
        return best_offer


@dataclass(frozen=True, slots=True)
class TaskMap:
    name: str
    normalized_name: str


@dataclass(frozen=True, slots=True)
class TaskItemRequirement:
    item_names: tuple[str, ...]
    count: float
    found_in_raid: bool
    description: str


@dataclass(frozen=True, slots=True)
class TaskObjective:
    objective_type: str
    description: str
    optional: bool
    maps: tuple[TaskMap, ...]
    item_requirements: tuple[TaskItemRequirement, ...]
    required_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskRewardItem:
    item_name: str
    count: float


@dataclass(frozen=True, slots=True)
class TaskRewards:
    items: tuple[TaskRewardItem, ...]
    trader_standing: tuple[str, ...]
    skills: tuple[str, ...]
    unlocks: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.items or self.trader_standing or self.skills or self.unlocks)


@dataclass(frozen=True, slots=True)
class TarkovTask:
    id: str
    name: str
    trader_name: str
    min_player_level: int | None
    maps: tuple[TaskMap, ...]
    objectives: tuple[TaskObjective, ...]
    prerequisite_names: tuple[str, ...]
    required_items: tuple[TaskItemRequirement, ...]
    required_keys: tuple[str, ...]
    experience: int
    wiki_url: str | None
    image_url: str | None
    kappa_required: bool
    lightkeeper_required: bool
    faction_name: str | None
    available_delay_min_seconds: int
    available_delay_max_seconds: int
    start_rewards: TaskRewards
    finish_rewards: TaskRewards
    failure_rewards: TaskRewards


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class QuestLogMatch:
    task: TarkovTask
    source_text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class AggregatedTaskItem:
    item_names: tuple[str, ...]
    count: float
    found_in_raid: bool
    task_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AggregatedTaskKey:
    key_name: str
    task_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuestLogMapGroup:
    task_map: TaskMap
    task_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuestLogSummary:
    matches: tuple[QuestLogMatch, ...]
    item_requirements: tuple[AggregatedTaskItem, ...]
    required_keys: tuple[AggregatedTaskKey, ...]
    map_groups: tuple[QuestLogMapGroup, ...]
    tasks_without_map: tuple[str, ...]
    unmatched_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServerComponentStatus:
    name: str
    message: str | None
    status: int
    status_code: str


@dataclass(frozen=True, slots=True)
class ServerStatusMessage:
    content: str
    time: str
    message_type: int
    solve_time: str | None
    status_code: str

    @property
    def is_active(self) -> bool:
        return self.solve_time is None


@dataclass(frozen=True, slots=True)
class TarkovServerStatus:
    general_status: ServerComponentStatus
    components: tuple[ServerComponentStatus, ...]
    messages: tuple[ServerStatusMessage, ...]


@dataclass(frozen=True, slots=True)
class RecipeIngredient:
    item_id: str
    item_name: str
    count: float
    reusable: bool
    snapshot_flea_price: int | None
    trader_price: int | None


@dataclass(frozen=True, slots=True)
class ProfitRecipe:
    id: str
    recipe_type: str
    result_item_id: str
    result_item_name: str
    result_count: float
    result_snapshot_flea_price: int | None
    result_trader_price: int | None
    ingredients: tuple[RecipeIngredient, ...]
    source_name: str
    source_level: int | None
    task_unlock_name: str | None
    duration_seconds: int | None
    buy_limit: int | None


@dataclass(frozen=True, slots=True)
class PriceHistoryPoint:
    minimum_price: int
    offer_count: int | None
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class PricedIngredient:
    item_name: str
    count: float
    unit_price: int
    total_price: int
    reusable: bool


@dataclass(frozen=True, slots=True)
class ProfitResult:
    recipe_id: str
    recipe_type: str
    result_item_name: str
    result_count: float
    result_unit_price: int
    result_value: int
    result_price_source: str
    ingredients: tuple[PricedIngredient, ...]
    material_cost: int
    tool_capital: int
    gross_profit: int
    roi_percent: float | None
    profit_per_hour: int | None
    source_name: str
    source_level: int | None
    task_unlock_name: str | None
    duration_seconds: int | None
    buy_limit: int | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TraderFlipCandidate:
    item_id: str
    item_name: str
    trader_name: str
    trader_price: int
    snapshot_flea_price: int
    min_trader_level: int | None
    required_player_level: int | None
    task_unlock_name: str | None
    buy_limit: int | None
    restock_amount: int | None


@dataclass(frozen=True, slots=True)
class TraderFlipResult:
    item_name: str
    trader_name: str
    trader_price: int
    flea_price: int
    price_source: str
    gross_profit_each: int
    roi_percent: float
    min_trader_level: int | None
    required_player_level: int | None
    task_unlock_name: str | None
    buy_limit: int | None
    restock_amount: int | None
    low_liquidity: bool
