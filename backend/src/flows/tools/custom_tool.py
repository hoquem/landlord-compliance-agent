
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class PlaceholderToolInput(BaseModel):
    """Input schema for PlaceholderTool.

    Placeholder scaffold; replace with a real tool (or delete this file)
    when Task 11 builds the CategoriseStatementFlow.
    """

    argument: str = Field(..., description="Description of the argument.")


class PlaceholderTool(BaseTool):
    """Placeholder tool scaffold; not wired into any agent yet."""

    name: str = "Placeholder tool"
    description: str = "Placeholder tool kept only to preserve the tools/ scaffold layout."
    args_schema: type[BaseModel] = PlaceholderToolInput

    def _run(self, argument: str) -> str:
        raise NotImplementedError("Placeholder tool scaffold; not implemented.")
