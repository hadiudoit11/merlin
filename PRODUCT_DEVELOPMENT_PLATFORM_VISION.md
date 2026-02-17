# Product Development Platform - Complete Vision

## The Big Idea

**Canvas is just the visualization**. The real product is a **Development Wizard** that:
- Guides teams through product development stages
- Ingests information from all sources (Jira, Zoom, docs, Slack)
- **Stages changes** for approval before updating artifacts
- Shows **impact analysis** - how changes ripple through the project
- Keeps all artifacts (PRD, specs, designs, timelines) in sync

---

## The Workflow Stages

```
1. Research          → Understand the problem
2. PRD Review        → Define what to build
3. UX/Experience     → Design the solution
4. Tech Spec         → Plan the implementation
5. Project Kickoff   → Align team & resources
6. Development       → Build the product
7. QA                → Verify quality
8. Launch            → Ship to users
9. Retrospective     → Learn & improve
```

Each stage has:
- **Artifacts** (documents, designs, code)
- **Stakeholders** (who needs to approve)
- **Inputs** (information sources)
- **Outputs** (deliverables)
- **Exit Criteria** (when to move forward)

---

## Event-Driven Architecture

### The Core Loop

```
New Information Arrives
  ↓
AI Analyzes Impact
  ↓
Stages Proposed Changes
  ↓
Human Approves/Rejects
  ↓
Artifacts Update
  ↓
Canvas Visualizes
```

### Example: Jira Issue Created

```
Event: New Jira ticket "PROJ-456: Add OAuth login"
  ↓
AI Detects: This affects multiple artifacts
  ├─ PRD: New feature requirement
  ├─ Tech Spec: Authentication architecture change
  ├─ Timeline: +2 weeks of work
  └─ UX Designs: Login flow needs update
  ↓
Staging Area: Shows proposed changes
  ┌───────────────────────────────────────────┐
  │ 🔔 PROJ-456 affects 4 artifacts           │
  │                                           │
  │ 📄 PRD v2.3 → v2.4 (Draft)                │
  │ + Add "OAuth Login" to Features section  │
  │ + Update success metrics                 │
  │ ✓ Approve  ✗ Reject  👁️ Preview         │
  │                                           │
  │ 🎨 UX Flow v1.2 → v1.3 (Draft)            │
  │ + Add OAuth provider selection screen    │
  │ ✓ Approve  ✗ Reject  👁️ Preview         │
  │                                           │
  │ 📋 Timeline                                │
  │ Sprint 5: +2 weeks (Now: Aug 15 → Aug 29)│
  │ ✓ Approve  ✗ Reject  👁️ Details         │
  └───────────────────────────────────────────┘
  ↓
PM Reviews & Approves PRD + Timeline
Designer Reviews & Rejects UX (wants custom design)
  ↓
Approved changes apply
Canvas updates automatically
Notifications sent to team
```

---

## Architecture Design

### 1. Workflow State Machine

```python
# app/models/workflow.py

class WorkflowStage(str, enum.Enum):
    RESEARCH = "research"
    PRD_REVIEW = "prd_review"
    UX_REVIEW = "ux_review"
    TECH_SPEC = "tech_spec"
    KICKOFF = "kickoff"
    DEVELOPMENT = "development"
    QA = "qa"
    LAUNCH = "launch"
    RETROSPECTIVE = "retrospective"

class Project(Base):
    """A product development project (higher level than Canvas)"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    current_stage = Column(Enum(WorkflowStage), default=WorkflowStage.RESEARCH)

    # Timeline
    start_date = Column(DateTime)
    target_launch_date = Column(DateTime)
    actual_launch_date = Column(DateTime)

    # Metadata
    product_owner_id = Column(Integer, ForeignKey("users.id"))
    tech_lead_id = Column(Integer, ForeignKey("users.id"))
    design_lead_id = Column(Integer, ForeignKey("users.id"))

    # Relationships
    canvases = relationship("Canvas", back_populates="project")  # Multiple canvases per project
    artifacts = relationship("Artifact", back_populates="project")
    stage_transitions = relationship("StageTransition", back_populates="project")
    change_proposals = relationship("ChangeProposal", back_populates="project")

class StageTransition(Base):
    """Track when project moves between stages"""
    __tablename__ = "stage_transitions"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    from_stage = Column(Enum(WorkflowStage))
    to_stage = Column(Enum(WorkflowStage))
    transition_date = Column(DateTime, default=datetime.utcnow)
    approved_by_id = Column(Integer, ForeignKey("users.id"))
    notes = Column(Text)

    # Exit criteria checklist
    exit_criteria_met = Column(JSON)  # {"prd_approved": true, "stakeholders_aligned": true}
```

### 2. Artifact Management

```python
# app/models/artifact.py

class ArtifactType(str, enum.Enum):
    PRD = "prd"
    TECH_SPEC = "tech_spec"
    UX_DESIGN = "ux_design"
    TIMELINE = "timeline"
    KICKOFF_DECK = "kickoff_deck"
    TEST_PLAN = "test_plan"
    LAUNCH_PLAN = "launch_plan"
    RETRO_NOTES = "retro_notes"

class Artifact(Base):
    """Versioned artifacts (PRD, specs, designs, etc.)"""
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    artifact_type = Column(Enum(ArtifactType), nullable=False)

    # Versioning
    version = Column(String(20))  # "v1.0", "v2.3"
    status = Column(String(20))   # "draft", "review", "approved", "archived"

    # Content
    content = Column(Text)  # Rich text content
    metadata = Column(JSON)  # Type-specific metadata

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"))
    approvers = Column(JSON)  # List of user IDs who need to approve
    approved_by = Column(JSON)  # List of user IDs who have approved

    # Links
    canvas_node_id = Column(Integer, ForeignKey("nodes.id"))  # Link to canvas visualization

    # Relationships
    versions = relationship("ArtifactVersion", back_populates="artifact")
    change_proposals = relationship("ChangeProposal", back_populates="artifact")

class ArtifactVersion(Base):
    """Version history for artifacts"""
    __tablename__ = "artifact_versions"

    id = Column(Integer, primary_key=True)
    artifact_id = Column(Integer, ForeignKey("artifacts.id"))
    version = Column(String(20))
    content = Column(Text)
    created_by_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    change_summary = Column(Text)  # What changed in this version
```

### 3. Change Staging System

```python
# app/models/change_proposal.py

class ChangeProposal(Base):
    """Proposed changes waiting for approval"""
    __tablename__ = "change_proposals"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    artifact_id = Column(Integer, ForeignKey("artifacts.id"))

    # Source
    triggered_by_type = Column(String(50))  # "jira_issue", "zoom_meeting", "slack_message"
    triggered_by_id = Column(String(255))   # External ID (PROJ-456, zoom-123, etc.)
    source_event_id = Column(Integer, ForeignKey("input_events.id"))

    # Proposed Change
    change_type = Column(String(50))  # "add_section", "update_timeline", "new_requirement"
    proposed_diff = Column(JSON)  # Before/after diff
    ai_rationale = Column(Text)   # Why AI is proposing this
    impact_analysis = Column(JSON)  # What else is affected

    # Approval
    status = Column(String(20), default="pending")  # "pending", "approved", "rejected", "merged"
    assigned_to_id = Column(Integer, ForeignKey("users.id"))  # Who needs to review
    reviewed_by_id = Column(Integer, ForeignKey("users.id"))
    reviewed_at = Column(DateTime)
    review_notes = Column(Text)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    auto_apply_at = Column(DateTime)  # Auto-approve after X days if no review

class ImpactAnalysis(Base):
    """Cross-artifact impact tracking"""
    __tablename__ = "impact_analyses"

    id = Column(Integer, primary_key=True)
    change_proposal_id = Column(Integer, ForeignKey("change_proposals.id"))

    # What's affected
    affected_artifact_id = Column(Integer, ForeignKey("artifacts.id"))
    impact_type = Column(String(50))  # "content_change", "timeline_shift", "dependency_added"
    severity = Column(String(20))  # "low", "medium", "high", "critical"

    # Details
    description = Column(Text)
    suggested_action = Column(Text)
```

### 4. Workflow Orchestrator

```python
# app/services/workflow_orchestrator.py

class WorkflowOrchestrator:
    """Orchestrates the product development workflow"""

    async def process_new_input(
        self,
        session: AsyncSession,
        input_event: InputEvent,
        project_id: int
    ):
        """
        When new information arrives (Jira, Zoom, etc.):
        1. Analyze which artifacts it affects
        2. Generate proposed changes
        3. Create change proposals
        4. Notify stakeholders
        """

        # 1. Determine current stage and relevant artifacts
        project = await self.get_project(session, project_id)
        relevant_artifacts = await self.get_stage_artifacts(session, project.current_stage)

        # 2. AI analyzes impact
        impact_analysis = await self.analyze_impact(input_event, relevant_artifacts)

        # 3. Generate change proposals for each affected artifact
        proposals = []
        for artifact, impact in impact_analysis.items():
            if impact["severity"] in ("medium", "high", "critical"):
                proposal = await self.create_change_proposal(
                    session,
                    artifact=artifact,
                    input_event=input_event,
                    impact=impact
                )
                proposals.append(proposal)

        # 4. Notify stakeholders
        await self.notify_stakeholders(proposals)

        return proposals

    async def analyze_impact(
        self,
        input_event: InputEvent,
        artifacts: List[Artifact]
    ) -> Dict[Artifact, Dict]:
        """
        AI-powered impact analysis.

        Example: New Jira issue "Add OAuth login"

        Returns:
        {
            prd_artifact: {
                "severity": "high",
                "impact_type": "new_requirement",
                "rationale": "OAuth is a new feature that needs to be documented",
                "suggested_changes": [
                    {"section": "Features", "action": "add", "content": "OAuth Login..."},
                    {"section": "Success Metrics", "action": "update", ...}
                ]
            },
            tech_spec_artifact: {
                "severity": "high",
                "impact_type": "architecture_change",
                ...
            },
            timeline_artifact: {
                "severity": "medium",
                "impact_type": "timeline_extension",
                "suggested_changes": [
                    {"sprint": 5, "add_weeks": 2, "reason": "OAuth implementation"}
                ]
            }
        }
        """

        # Use AI to analyze (Claude/GPT)
        prompt = f"""
        Analyze how this input affects the product development artifacts.

        Input Event:
        - Type: {input_event.source_type}
        - Event: {input_event.event_type}
        - Data: {input_event.payload}

        Current Artifacts:
        {self.format_artifacts_for_ai(artifacts)}

        For each artifact, determine:
        1. Is it affected? (yes/no)
        2. Severity (low/medium/high/critical)
        3. Type of impact (new_requirement, timeline_change, scope_change, etc.)
        4. Specific changes needed
        5. Rationale

        Return JSON.
        """

        # Call AI (using existing template system)
        analysis = await self.call_ai_analysis(prompt)
        return analysis

    async def create_change_proposal(
        self,
        session: AsyncSession,
        artifact: Artifact,
        input_event: InputEvent,
        impact: Dict
    ) -> ChangeProposal:
        """Create a staged change for review"""

        # Generate diff (before/after)
        current_content = artifact.content
        proposed_content = await self.generate_updated_content(
            current_content,
            impact["suggested_changes"]
        )

        diff = {
            "before": current_content,
            "after": proposed_content,
            "changes": impact["suggested_changes"]
        }

        # Determine who should review
        reviewer = await self.determine_reviewer(artifact, impact["severity"])

        # Create proposal
        proposal = ChangeProposal(
            project_id=artifact.project_id,
            artifact_id=artifact.id,
            triggered_by_type=input_event.source_type,
            triggered_by_id=input_event.external_id,
            source_event_id=input_event.id,
            change_type=impact["impact_type"],
            proposed_diff=diff,
            ai_rationale=impact["rationale"],
            impact_analysis=impact.get("cross_artifact_impact", {}),
            assigned_to_id=reviewer.id
        )

        session.add(proposal)
        await session.flush()

        return proposal
```

---

## User Experience Design

### 1. Project Dashboard

```
┌─ Project: Mobile App Redesign ────────────────────────────┐
│                                                            │
│ Stage: 🎨 UX Review (3 of 9)                    [Timeline]│
│ ████████████░░░░░░░░░░░░░░░░░░░░░░░░  33%                 │
│                                                            │
│ ┌─ Current Stage ──────────────────────────────────────┐  │
│ │ 🎨 UX/Experience Review                              │  │
│ │                                                       │  │
│ │ Tasks:                                                │  │
│ │ ✓ User research complete                             │  │
│ │ ⋯ Design mockups (80% complete)                      │  │
│ │ ☐ Accessibility review                               │  │
│ │ ☐ Stakeholder approval                               │  │
│ │                                                       │  │
│ │ Exit Criteria: 4/6 met                                │  │
│ │ [Review & Advance Stage →]                           │  │
│ └───────────────────────────────────────────────────────┘  │
│                                                            │
│ ┌─ Pending Changes (3) ──────────────────────────────┐    │
│ │ 🔔 PROJ-456: Add OAuth login                        │    │
│ │ Affects: PRD, Tech Spec, Timeline                   │    │
│ │ [Review Changes →]                                  │    │
│ │                                                      │    │
│ │ 🔔 Zoom: Stakeholder feedback meeting                │    │
│ │ Affects: UX Designs                                 │    │
│ │ [Review Changes →]                                  │    │
│ └──────────────────────────────────────────────────────┘    │
│                                                            │
│ ┌─ Artifacts ───────────────────────────────────────────┐  │
│ │ 📄 PRD v2.3 (Approved) - Updated 2 days ago          │  │
│ │ 🎨 UX Designs v1.2 (Draft) - Updated 1 hour ago      │  │
│ │ 🔧 Tech Spec v1.0 (Review) - Updated 3 days ago      │  │
│ │ 📅 Timeline (Live) - Launch: Aug 15, 2026            │  │
│ └───────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 2. Change Approval UI

```
┌─ Change Proposal: PROJ-456 ─────────────────────────────┐
│                                                          │
│ Source: Jira Issue Created                              │
│ PROJ-456: Add OAuth login support                       │
│                                                          │
│ AI Analysis: This feature affects multiple artifacts    │
│ Confidence: High (89%)                                   │
│                                                          │
│ ┌─ Impact Summary ────────────────────────────────────┐  │
│ │ 📄 PRD v2.3 → v2.4                  [High Impact]   │  │
│ │ 🔧 Tech Spec v1.0 → v1.1            [High Impact]   │  │
│ │ 📅 Timeline                          [Medium Impact]│  │
│ │ 🎨 UX Designs v1.2 → v1.3           [Low Impact]    │  │
│ └──────────────────────────────────────────────────────┘  │
│                                                          │
│ ┌─ Proposed Changes ─────────────────────────────────┐   │
│ │                                                     │   │
│ │ Tab: [PRD] [Tech Spec] [Timeline] [UX Designs]     │   │
│ │                                                     │   │
│ │ PRD v2.3 → v2.4 (Draft):                           │   │
│ │                                                     │   │
│ │ Section: Features                                   │   │
│ │ + **OAuth Login**                                   │   │
│ │ + Users can sign in using Google, GitHub, or       │   │
│ │ + Microsoft OAuth providers.                        │   │
│ │                                                     │   │
│ │ Section: Success Metrics                            │   │
│ │ ~ Sign-up conversion rate: 25% → 40%               │   │
│ │ + OAuth reduces friction in onboarding             │   │
│ │                                                     │   │
│ │ AI Rationale:                                       │   │
│ │ "OAuth is a new authentication method that needs    │   │
│ │  to be added to the Features section. Updated      │   │
│ │  success metrics based on industry benchmarks."     │   │
│ │                                                     │   │
│ │ [Preview Full Document] [View Diff]                │   │
│ └─────────────────────────────────────────────────────┘   │
│                                                          │
│ ┌─ Cross-Artifact Impact ──────────────────────────────┐  │
│ │ If you approve this PRD change:                      │  │
│ │ • Tech Spec needs authentication architecture update │  │
│ │ • Timeline extends by ~2 weeks (Sprint 5)            │  │
│ │ • UX Designs need OAuth provider selection screen    │  │
│ │                                                      │  │
│ │ Recommendation: Review all 4 artifacts together      │  │
│ └──────────────────────────────────────────────────────┘  │
│                                                          │
│ Actions:                                                 │
│ [✓ Approve All]  [✗ Reject All]  [Review Individually] │
└──────────────────────────────────────────────────────────┘
```

### 3. Timeline Impact Visualization

```
┌─ Timeline Impact: PROJ-456 ─────────────────────────────┐
│                                                          │
│ Current Timeline:                                        │
│ ════════════════════════════════════════►                │
│ Jul 1   Jul 15   Aug 1   Aug 15   Sep 1                 │
│   │       │        │        │       │                    │
│   Research  PRD   UX    Dev    Launch                    │
│                                  ^                        │
│                           Target: Aug 15                  │
│                                                          │
│ Proposed Timeline (with PROJ-456):                       │
│ ══════════════════════════════════════════════►          │
│ Jul 1   Jul 15   Aug 1   Aug 15   Aug 29   Sep 1        │
│   │       │        │        │        │       │           │
│   Research  PRD   UX    Dev    OAuth    Launch           │
│                                              ^            │
│                                   New Target: Aug 29      │
│                                                          │
│ Impact:                                                   │
│ • Sprint 5 extended: +2 weeks                            │
│ • Launch date: Aug 15 → Aug 29 (+14 days)                │
│ • Affects: Q3 goals, marketing launch campaign           │
│                                                          │
│ Recommendation:                                           │
│ ⚠️  This pushes launch past Q3 deadline                  │
│ Consider: Defer OAuth to v2.0 OR negotiate deadline      │
│                                                          │
│ [Approve New Timeline]  [Defer Feature]  [Discuss]      │
└──────────────────────────────────────────────────────────┘
```

---

## Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)
- [ ] Create Project, Artifact, ChangeProposal models
- [ ] Build basic workflow state machine
- [ ] Design staging area UI
- [ ] Implement impact analysis service

### Phase 2: AI Integration (Weeks 3-4)
- [ ] AI impact analyzer (which artifacts affected)
- [ ] AI change generator (proposed diffs)
- [ ] AI rationale writer (why changes matter)
- [ ] Connect to existing template system

### Phase 3: Approval Workflow (Weeks 5-6)
- [ ] Change proposal review UI
- [ ] Approval/rejection flow
- [ ] Diff viewer for before/after
- [ ] Notification system

### Phase 4: Stage Management (Weeks 7-8)
- [ ] Stage transition UI
- [ ] Exit criteria checklist
- [ ] Artifact creation wizards
- [ ] Timeline visualization

### Phase 5: Integration & Polish (Weeks 9-10)
- [ ] Connect all input sources (Jira, Zoom, Slack)
- [ ] Canvas visualization updates
- [ ] Real-time collaboration
- [ ] Analytics & insights

---

## Key Differentiators

### vs. Linear/Jira
- **Workflow-first**: Not just task tracking, full SDLC orchestration
- **AI-powered**: Auto-detects impacts, suggests changes
- **Approval-based**: Changes staged, not auto-applied

### vs. Notion/Confluence
- **Structured**: Enforces stages and artifacts
- **Event-driven**: Updates flow from external sources
- **Impact-aware**: Shows cross-artifact dependencies

### vs. Productboard/Aha
- **Technical**: Goes beyond product planning into dev/QA/launch
- **Integrated**: Single source of truth for all artifacts
- **Adaptive**: Learns from inputs, keeps artifacts current

---

## Next Steps

**I can start building this by:**

1. **Creating the data models** (Project, Artifact, ChangeProposal)
2. **Building the WorkflowOrchestrator service**
3. **Designing the staging area UI**
4. **Implementing a simple flow**: Jira issue → Impact analysis → Staged changes → Approval

**Want me to start building Phase 1?**
