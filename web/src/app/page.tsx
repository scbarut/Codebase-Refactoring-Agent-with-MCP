export default function Home() {
  return (
    <div className="container">
      <header className="hero">
        <h1>Autonomous Code Modernization</h1>
        <p>
          Ingest Python codebases, identify deprecated libraries, review migration plans with human-in-the-loop control, and self-heal test regressions in isolated sandboxes.
        </p>
      </header>

      <section className="grid">
        <div className="card">
          <h3>HITL Gateway & Risk Analysis</h3>
          <p>
            Deterministic static AST scanning identifies affected symbols and classifies risks before touching a single file. Review and approve the plan before execution.
          </p>
          <span className="tech-tag">LangGraph</span>
          <span className="tech-tag">PostgreSQL Checkpointer</span>
        </div>

        <div className="card">
          <h3>Model Context Protocol</h3>
          <p>
            Decoupled tool services via stdio/HTTP protocols: AST manipulation with libcst, documentation retrieval with pre-indexed corpus, and git branch isolation.
          </p>
          <span className="tech-tag">MCP Protocol</span>
          <span className="tech-tag">libcst</span>
        </div>

        <div className="card">
          <h3>Docker Sandbox & Self-Healing</h3>
          <p>
            Per-job ephemeral container sandbox running Python 3.9–3.12 with pytest. Automatically extracts tracebacks, retrieves docs, and iterates up to 3 times.
          </p>
          <span className="tech-tag">Docker</span>
          <span className="tech-tag">Pytest Sandbox</span>
        </div>
      </section>
    </div>
  );
}
