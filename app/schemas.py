from pydantic import BaseModel, Field, field_validator


class RegisterInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name", "username", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class ConnectionRequestInput(BaseModel):
    public_id: str = Field(min_length=3, max_length=20)


class RequestResponseInput(BaseModel):
    action: str = Field(pattern=r"^(accept|reject)$")


class GroupCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    member_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("member_ids")
    @classmethod
    def normalize_members(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
        if not normalized:
            raise ValueError("Choose at least one member")
        return normalized


class GroupMembersInput(BaseModel):
    member_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("member_ids")
    @classmethod
    def normalize_members(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
        if not normalized:
            raise ValueError("Choose at least one member")
        return normalized
