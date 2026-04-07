import os
from typing import List

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew as crew_project, task
import litellm
from backend.api.services.llm_monitor import log_llm_call

from game_studio.tools import (
    ExecuteSQLQueryTool,
    SaveChartDataTool,
    SaveMetricsTool,
    ValidateDatabaseConnectionTool,
    SyncToClickHouseTool,
    ClickHouseQueryTool,
    GetClickHouseSchemaTool,
    GetClickHouseTableNamesTool,
)


@CrewBase
class GameStudio:
    """Database analysis multi-agent crew."""

    _verbose = os.getenv("DEBUG_MODE", "False").lower() == "true"

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    
    def _setup_llm_monitoring(self):
        """Global registration of LiteLLM callbacks for debug monitoring."""
        if hasattr(GameStudio, "_monitoring_setup_done"):
            return

        def capture_success(kwargs, completion_response, start_time, end_time):
            messages = kwargs.get("messages", [])
            model_used = kwargs.get("model", "unknown")
            usage = getattr(completion_response, "usage", {})
            if hasattr(usage, "to_dict"): usage = usage.to_dict()
            
            log_llm_call(
                model=model_used,
                messages=messages,
                response=completion_response.to_dict() if hasattr(completion_response, "to_dict") else str(completion_response),
                usage=usage,
                metadata={"status": "success"}
            )

        def capture_failure(kwargs, exception, start_time, end_time):
            log_llm_call(
                model=kwargs.get("model", "unknown"),
                messages=kwargs.get("messages", []),
                response=str(exception),
                metadata={"status": "failure"}
            )

        litellm.success_callback.append(capture_success)
        litellm.failure_callback.append(capture_failure)
        GameStudio._monitoring_setup_done = True

    def llm(self) -> LLM:
        model = os.getenv("MODEL", "groq/llama-3.3-70b-versatile")
        self._setup_llm_monitoring()
        return LLM(model=model, max_tokens=8192)

    def llm_extended(self) -> LLM:
        """LLM with higher token limit for agents that produce large outputs
        (e.g. visualization expert). Reasoning models spend tokens on internal
        thinking, so we need extra headroom to avoid finish_reason='length'
        truncation that causes empty/None responses."""
        model = os.getenv("MODEL", "groq/llama-3.3-70b-versatile")
        self._setup_llm_monitoring()
        return LLM(model=model, max_tokens=32768)

    @agent
    def db_connection_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config["db_connection_engineer"],  # type: ignore[index]
            tools=[ValidateDatabaseConnectionTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def schema_documenter(self) -> Agent:
        return Agent(
            config=self.agents_config["schema_documenter"],  # type: ignore[index]
            tools=[GetClickHouseTableNamesTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def table_data_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config["table_data_extractor"],  # type: ignore[index]
            tools=[SyncToClickHouseTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def schema_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["schema_analyst"],  # type: ignore[index]
            tools=[GetClickHouseSchemaTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def data_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["data_analyst"],  # type: ignore[index]
            tools=[ClickHouseQueryTool(), GetClickHouseSchemaTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def question_normalizer(self) -> Agent:
        return Agent(
            config=self.agents_config["question_normalizer"],  # type: ignore[index]
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def query_dispatcher(self) -> Agent:
        return Agent(
            config=self.agents_config["query_dispatcher"],  # type: ignore[index]
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def suggestions_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["suggestions_agent"],  # type: ignore[index]
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def visual_reporter(self) -> Agent:
        return Agent(
            config=self.agents_config["visual_reporter"],  # type: ignore[index]
            tools=[SaveChartDataTool(), SaveMetricsTool()],
            llm=self.llm_extended(),
            verbose=self._verbose,
        )

    @agent
    def response_synthesizer(self) -> Agent:
        return Agent(
            config=self.agents_config["response_synthesizer"],  # type: ignore[index]
            llm=self.llm(),
            verbose=self._verbose,
        )

    @agent
    def dashboard_architect(self) -> Agent:
        return Agent(
            config=self.agents_config["dashboard_architect"],  # type: ignore[index]
            tools=[GetClickHouseTableNamesTool(), GetClickHouseSchemaTool()],
            llm=self.llm(),
            verbose=self._verbose,
        )

    @task
    def validate_connection_task(self) -> Task:
        return Task(
            config=self.tasks_config["validate_connection_task"],  # type: ignore[index]
            agent=self.db_connection_engineer(),
        )

    @task
    def normalize_question_task(self) -> Task:
        return Task(
            config=self.tasks_config["normalize_question_task"],  # type: ignore[index]
            agent=self.question_normalizer(),
        )
 
    @task
    def resolve_join_path_task(self) -> Task:
        return Task(
            config=self.tasks_config["resolve_join_path_task"],  # type: ignore[index]
            agent=self.schema_analyst(),
            context=[self.analyze_schema_relevance_task()],
        )

    @task
    def extract_schema_task(self) -> Task:
        return Task(
            config=self.tasks_config["extract_schema_task"],  # type: ignore[index]
            agent=self.schema_documenter(),
            context=[self.validate_connection_task()],
        )

    @task
    def extract_table_data_task(self) -> Task:
        return Task(
            config=self.tasks_config["extract_table_data_task"],  # type: ignore[index]
            agent=self.table_data_extractor(),
            context=[self.extract_schema_task()],
        )

    @task
    def analyze_schema_relevance_task(self) -> Task:
        return Task(
            config=self.tasks_config["analyze_schema_relevance_task"],  # type: ignore[index]
            agent=self.schema_analyst(),
            context=[self.extract_schema_task(), self.normalize_question_task()],
        )

    @task
    def plan_query_dispatch_task(self) -> Task:
        return Task(
            config=self.tasks_config["plan_query_dispatch_task"],  # type: ignore[index]
            agent=self.query_dispatcher(),
            context=[self.analyze_schema_relevance_task(), self.resolve_join_path_task()],
        )

    @task
    def analyze_and_compute_task(self) -> Task:
        return Task(
            config=self.tasks_config["analyze_and_compute_task"],  # type: ignore[index]
            agent=self.data_analyst(),
            context=[
                self.normalize_question_task(),
                self.extract_schema_task(),
                self.analyze_schema_relevance_task(),
                self.resolve_join_path_task(),
                self.plan_query_dispatch_task()
            ],
        )

    @task
    def visual_report_task(self) -> Task:
        return Task(
            config=self.tasks_config["visual_report_task"],  # type: ignore[index]
            agent=self.visual_reporter(),
            context=[self.analyze_and_compute_task()],
        )

    @task
    def synthesize_response_task(self) -> Task:
        return Task(
            config=self.tasks_config["synthesize_response_task"],  # type: ignore[index]
            agent=self.response_synthesizer(),
            context=[self.analyze_and_compute_task(), self.visual_report_task()],
            output_file="output/final_response.md",
        )

    @task
    def generate_followup_suggestions_task(self) -> Task:
        return Task(
            config=self.tasks_config["generate_followup_suggestions_task"],  # type: ignore[index]
            agent=self.suggestions_agent(),
            context=[self.analyze_and_compute_task(), self.synthesize_response_task()],
        )

    @task
    def suggest_initial_questions_task(self) -> Task:
        return Task(
            config=self.tasks_config["suggest_initial_questions_task"],  # type: ignore[index]
            agent=self.suggestions_agent(),
        )

    @task
    def architect_dashboard_task(self) -> Task:
        return Task(
            config=self.tasks_config["architect_dashboard_task"],  # type: ignore[index]
            agent=self.dashboard_architect(),
        )

    @crew_project
    def crew(self) -> Crew:
        """Creates the main GameStudio analytical crew."""
        return Crew(
            agents=[
                self.question_normalizer(),
                self.schema_documenter(),
                self.schema_analyst(),
                self.query_dispatcher(),
                self.data_analyst(),
                self.visual_reporter(),
                self.response_synthesizer()
            ],
            tasks=[
                self.normalize_question_task(),
                self.extract_schema_task(),
                self.analyze_schema_relevance_task(),
                self.resolve_join_path_task(),
                self.plan_query_dispatch_task(),
                self.analyze_and_compute_task(),
                self.visual_report_task(),
                self.synthesize_response_task(),
                self.generate_followup_suggestions_task()
            ],
            process=Process.sequential,
            verbose=self._verbose,
            tracing=True,
        )

    @crew_project
    def setup_crew(self) -> Crew:
        """Crew for database connection and initial extraction."""
        return Crew(
            agents=[self.db_connection_engineer(), self.table_data_extractor()],
            tasks=[self.validate_connection_task(), self.extract_table_data_task()],
            process=Process.sequential,
            verbose=self._verbose,
            tracing=True,
        )

    @crew_project
    def suggestions_crew(self) -> Crew:
        """Crew for generating initial analytical suggestions."""
        return Crew(
            agents=[self.suggestions_agent()],
            tasks=[self.suggest_initial_questions_task()],
            process=Process.sequential,
            verbose=self._verbose,
            tracing=True,
        )

    @crew_project
    def dashboard_planner_crew(self) -> Crew:
        """Crew for planning dashboard widgets."""
        return Crew(
            agents=[self.dashboard_architect()],
            tasks=[self.architect_dashboard_task()],
            process=Process.sequential,
            verbose=self._verbose,
            tracing=True,
        )
