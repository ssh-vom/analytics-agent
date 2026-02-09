from dataclasses import dataclass


@dataclass(frozen=True)
class BranchOptions:
    source_worldline_id: str
    from_event_id: str
    name: str | None = None
    append_events: bool = False
    carried_user_message: str | None = None


@dataclass(frozen=True)
class BranchResult:
    new_worldline_id: str
    thread_id: str
    source_worldline_id: str
    from_event_id: str
    name: str
    created_event_ids: tuple[str, ...] = ()
    switched: bool = False


class WorldlineService:
    def branch_from_event(self, options: BranchOptions) -> BranchResult:
        raise NotImplementedError
