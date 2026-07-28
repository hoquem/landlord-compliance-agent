from crewai.flow import Flow, listen, start
from pydantic import BaseModel

from src.flows.crews.placeholder_crew.placeholder_crew import PlaceholderCrew


class FlowState(BaseModel):
    """Placeholder state; replace with the real CategoriseStatementFlow state
    (see Task 11).
    """

    input_text: str = ""
    result: str = ""


class LandlordComplianceFlow(Flow[FlowState]):
    """Placeholder flow scaffold.

    Proves the Flow -> Crew wiring imports and loads cleanly. Replace with
    the real CategoriseStatementFlow in Task 11.
    """

    @start()
    def prepare_input(self):
        print("Preparing input")

    @listen(prepare_input)
    def run_crew(self):
        result = PlaceholderCrew().crew().kickoff(inputs={"input_text": self.state.input_text})
        self.state.result = result.raw


def kickoff():
    flow = LandlordComplianceFlow()
    flow.kickoff()


def plot():
    flow = LandlordComplianceFlow()
    flow.plot()


if __name__ == "__main__":
    kickoff()
