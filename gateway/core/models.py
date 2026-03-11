from dataclasses import dataclass


@dataclass
class SendTask:
    group_name: str
    content: str
    is_image: bool = False
