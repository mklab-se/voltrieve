from fastapi import FastAPI
from voltrieve_doors import DoorTool

NAME = "customer"
SCOPES = ("customer.tools", "runa.ask")
INSTRUCTIONS = ""


def tools(app: FastAPI) -> list[DoorTool]:
    return []
