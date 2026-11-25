from fastapi import FastAPI
from voltrieve_doors import DoorTool

NAME = "car"
SCOPES = ("car.remote",)
INSTRUCTIONS = ""


def tools(app: FastAPI) -> list[DoorTool]:
    return []
