from fastapi import FastAPI
from voltrieve_doors import DoorTool

NAME = "operations"
SCOPES = ("operations.read",)
INSTRUCTIONS = ""


def tools(app: FastAPI) -> list[DoorTool]:
    return []
