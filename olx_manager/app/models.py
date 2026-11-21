from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Money = Annotated[int, Field(strict=True, ge=0, le=10_000_000_000)]
Revision = Annotated[int, Field(strict=True, gt=0)]
Key = Annotated[str, StringConstraints(min_length=8, max_length=80, pattern=r"^[\w.-]+$")]
FIELDS = {
    "general": {"label": "Inne", "attributes": {}},
    "car_parts": {
        "label": "Części samochodowe",
        "attributes": {
            "manufacturer": "Producent",
            "model": "Model",
            "year": "Rocznik",
            "oem": "Numer OEM",
            "position": "Miejsce montażu",
            "originality": "Oryginalność",
        },
    },
    "electronics": {"label": "Elektronika", "attributes": {"brand": "Marka", "model": "Model"}},
    "tools": {"label": "Narzędzia", "attributes": {"brand": "Marka", "power": "Zasilanie"}},
    "furniture": {"label": "Meble", "attributes": {"material": "Materiał", "dimensions": "Wymiary"}},
    "appliances": {"label": "AGD", "attributes": {"brand": "Marka", "model": "Model"}},
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftFields(Model):
    title: Annotated[Short, Field(min_length=1)]
    description: str = Field("", max_length=20_000)
    condition: Short = ""
    category: Literal["general", "car_parts", "electronics", "tools", "furniture", "appliances"] = "general"
    category_hint: Short = ""
    attributes: dict[str, Short] = Field(default_factory=dict, max_length=30)
    price_minor: Money | None = None
    negotiable: bool = Field(False, strict=True)
    currency: Literal["PLN"] = "PLN"
    location: Short = ""
    delivery: Literal["pickup", "shipping", "both"] = "pickup"
    shipping_minor: Money | None = None
    notes: str = Field("", max_length=10_000)
    olx_category_id: int | None = Field(None, gt=0, strict=True)
    olx_city_id: int | None = Field(None, gt=0, strict=True)
    olx_district_id: int | None = Field(None, gt=0, strict=True)
    olx_contact_name: Short = ""
    olx_attributes: dict[str, Short] = Field(default_factory=dict, max_length=30)

    @field_validator("attributes", "olx_attributes")
    @classmethod
    def attribute_keys(cls, value):
        if any(not k or len(k) > 80 for k in value):
            raise ValueError("Nieprawidłowy klucz parametru")
        return value


class DraftChanges(Model):
    title: Short | None = None
    description: str | None = Field(None, max_length=20_000)
    condition: Short | None = None
    category: Literal["general", "car_parts", "electronics", "tools", "furniture", "appliances"] | None = None
    category_hint: Short | None = None
    attributes: dict[str, Short] | None = Field(None, max_length=30)
    price_minor: Money | None = None
    negotiable: bool | None = Field(None, strict=True)
    currency: Literal["PLN"] | None = None
    location: Short | None = None
    delivery: Literal["pickup", "shipping", "both"] | None = None
    shipping_minor: Money | None = None
    notes: str | None = Field(None, max_length=10_000)
    olx_category_id: int | None = Field(None, gt=0, strict=True)
    olx_city_id: int | None = Field(None, gt=0, strict=True)
    olx_district_id: int | None = Field(None, gt=0, strict=True)
    olx_contact_name: Short | None = None
    olx_attributes: dict[str, Short] | None = Field(None, max_length=30)


class PriceSuggestion(Model):
    price_minor: Money
    currency: Literal["PLN"] = "PLN"
    min_minor: Money | None = None
    max_minor: Money | None = None
    confidence: Literal["low", "medium", "high"] = "low"
    sources: list[Annotated[str, StringConstraints(max_length=1000, pattern=r"^https?://[^\s]+$")]] = Field(
        default_factory=list, max_length=10
    )
    notes: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def check_range(self):
        if self.min_minor is not None and self.min_minor > self.price_minor:
            raise ValueError("Dolna granica przekracza proponowaną cenę")
        if self.max_minor is not None and self.max_minor < self.price_minor:
            raise ValueError("Górna granica jest mniejsza od proponowanej ceny")
        if not self.sources and self.confidence != "low":
            raise ValueError("Wycena bez źródeł ma niską pewność")
        return self
