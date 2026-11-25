"""The door to a person's own car. These four tools are all there is: nothing here drives, parks or sees."""

from typing import Annotated, Literal

from fastapi import FastAPI
from pydantic import Field
from voltrieve_doors import DoorTool, Refused, caller

NAME = "car"
SCOPE = "car.remote"
SCOPES = (SCOPE,)
ROLE = "Car.Remote"
INSTRUCTIONS = (
    "The signed-in person's own Voltrieve, while its page is open and the driver has switched on Remote control. "
    "The car does something only when it stands still, and shows the driver what was done and by which app. "
    "It cannot be driven, parked or looked through from here."
)

NOT_LINKED = "No car is linked. The driver opens the car and switches on Remote control in its menu."
NOT_PARKED = "The car is not parked. It does this only when it stands still."
NO_ANSWER = "The car did not answer."
REFUSALS = {
    "not_linked": NOT_LINKED,
    "gone": NO_ANSWER,
    "no_answer": NO_ANSWER,
    "too_many": "That is more than the car does in a minute. Wait a little.",
    "moving": NOT_PARKED,
    "off": "The driver has switched Remote control off.",
    "no_sound": "The car could not make a sound. The driver has to click in the car first.",
    "unknown": "The car does not know how to do this.",
    "bad_arguments": "The car did not accept this.",
    "failed": "The car could not do this.",
}


def tools(app: FastAPI) -> list[DoorTool]:
    async def ask(command: str, **arguments) -> None:
        who = caller()
        code = await app.state.car_links.command(who.oid, command, arguments, who.app)
        if code != "done":
            raise Refused(REFUSALS[code])

    async def car_status() -> dict:
        link = app.state.car_links.latest(caller().oid)
        if link is None:
            return {"connected": False, "note": NOT_LINKED}
        return {"connected": True, "parked": link.parked, "odometer_km": link.odometer_km, "wipers": link.wipers}

    async def honk(times: Annotated[int, Field(strict=True, ge=1, le=3)] = 1) -> str:
        await ask("honk", times=times)
        return f"The car honked {times} time{'s' if times > 1 else ''}."

    async def flash_lights(times: Annotated[int, Field(strict=True, ge=1, le=5)] = 2) -> str:
        await ask("flash_lights", times=times)
        return f"The car flashes its lights {times} time{'s' if times > 1 else ''}."

    async def set_wipers(mode: Literal["off", "slow", "fast"]) -> str:
        await ask("set_wipers", mode=mode)
        return f"The wipers are {mode}."

    def tool(fn, title: str, description: str, read_only: bool = False, idempotent: bool = False) -> DoorTool:
        return DoorTool(fn.__name__, title, description, fn, roles=frozenset({ROLE}), scope=SCOPE,
                        read_only=read_only, idempotent=idempotent, people_only=True)

    return [
        tool(car_status, "Car status", "Whether the car is connected and parked, its odometer and its wipers.",
             read_only=True, idempotent=True),
        tool(honk, "Honk", "Sound the horn of the parked car, 1 to 3 times."),
        tool(flash_lights, "Flash the lights", "Flash the lights of the parked car, 1 to 5 times."),
        tool(set_wipers, "Set the wipers", "Set the windscreen wipers of the parked car: off, slow or fast.",
             idempotent=True),
    ]
