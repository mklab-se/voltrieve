"""The customer's car: made up, and the same one for everyone. The website and the car show it too."""

from app.context import ClientCar, ClientContext

MY_CAR = {
    "model": "Voltrieve Spark",
    "trim": "Plus",
    "drive": "RWD",
    "model_year": 2026,
    "registration": "SPARK",
    "odometer_km": 12400,
    "color": "Saffron",
    "next_service": "March 2028 or 30,000 km",
}


def garage_context() -> ClientContext:
    """What goes with a question to Runa, as from the website. The time of the customer is not known."""
    return ClientContext(car=ClientCar(**{field: MY_CAR[field] for field in ClientCar.model_fields}))
