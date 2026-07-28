from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class PlaceholderCrew:
    """Placeholder crew scaffold.

    Proves the CrewAI Flow -> Crew -> Agent/Task wiring imports and loads
    cleanly. Replace with the real compliance-categorisation crew in
    Task 11 (CategoriseStatementFlow).
    """

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def placeholder_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["placeholder_agent"],  # type: ignore[index]
        )

    @task
    def placeholder_task(self) -> Task:
        return Task(
            config=self.tasks_config["placeholder_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Placeholder Crew."""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
