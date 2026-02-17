# Product Development Platform - Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     PRODUCT DEVELOPMENT PLATFORM                 │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐   │
│  │   Input Layer  │  │  Orchestration │  │ Visualization  │   │
│  │   (Sources)    │→ │     Layer      │→ │    Layer       │   │
│  └────────────────┘  └────────────────┘  └────────────────┘   │
│          │                   │                    │             │
│          ↓                   ↓                    ↓             │
│   Jira, Zoom, Slack   Workflow Engine        Canvas UI         │
│   Confluence, Email   Impact Analyzer      Project Dashboard   │
│   GitHub, Linear      Change Staging       Approval Interface  │
└─────────────────────────────────────────────────────────────────┘
```

## Detailed Flow

```
┌─ INPUTS ─────────────────────────────────────────────────────────┐
│                                                                   │
│  Jira Issue Created: "PROJ-456: Add OAuth login"                │
│  Zoom Meeting: "Stakeholder feedback session"                    │
│  Slack Message: "@pm - users want dark mode"                     │
│  GitHub PR: "feat: implement new API endpoint"                   │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ↓
┌─ INPUT PROCESSOR ────────────────────────────────────────────────┐
│                                                                   │
│  InputEvent Created                                              │
│  - source_type: "jira"                                           │
│  - event_type: "issue_created"                                   │
│  - payload: {issue_key: "PROJ-456", ...}                         │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ↓
┌─ WORKFLOW ORCHESTRATOR ──────────────────────────────────────────┐
│                                                                   │
│  1. Identify Project & Current Stage                             │
│     → Project: "Mobile App Redesign"                             │
│     → Stage: "UX Review"                                          │
│                                                                   │
│  2. Get Relevant Artifacts                                       │
│     → PRD v2.3 (approved)                                        │
│     → Tech Spec v1.0 (draft)                                     │
│     → UX Designs v1.2 (review)                                   │
│     → Timeline (live)                                            │
│                                                                   │
│  3. AI Impact Analysis                                           │
│     ┌──────────────────────────────────────────────────┐        │
│     │ Prompt: "Analyze how PROJ-456 affects artifacts"│        │
│     │                                                  │        │
│     │ Context:                                         │        │
│     │ - Current PRD content                            │        │
│     │ - Current Tech Spec                              │        │
│     │ - Current Timeline                               │        │
│     │ - Jira issue details                             │        │
│     │                                                  │        │
│     │ Returns:                                         │        │
│     │ {                                                │        │
│     │   "prd": {                                       │        │
│     │     "severity": "high",                          │        │
│     │     "impact_type": "new_requirement",            │        │
│     │     "changes": [...]                             │        │
│     │   },                                             │        │
│     │   "tech_spec": {...},                            │        │
│     │   "timeline": {...}                              │        │
│     │ }                                                │        │
│     └──────────────────────────────────────────────────┘        │
│                                                                   │
│  4. Generate Change Proposals                                    │
│     → ChangeProposal #1: Update PRD                              │
│     → ChangeProposal #2: Update Tech Spec                        │
│     → ChangeProposal #3: Extend Timeline                         │
│                                                                   │
│  5. Assign Reviewers                                             │
│     → ChangeProposal #1 → Product Owner                          │
│     → ChangeProposal #2 → Tech Lead                              │
│     → ChangeProposal #3 → Project Manager                        │
│                                                                   │
│  6. Notify Stakeholders                                          │
│     → Email, Slack notification, In-app notification             │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ↓
┌─ STAGING AREA ────────────────────────────────────────────────────┐
│                                                                   │
│  ┌─ Pending Change #1 ─────────────────────────────────────────┐ │
│  │ Source: PROJ-456 (Jira)                                     │ │
│  │ Artifact: PRD v2.3 → v2.4                                   │ │
│  │ Assigned to: @sarah (Product Owner)                         │ │
│  │                                                              │ │
│  │ Proposed Changes:                                           │ │
│  │ + Add "OAuth Login" to Features section                     │ │
│  │ + Update Success Metrics                                    │ │
│  │                                                              │ │
│  │ AI Rationale:                                               │ │
│  │ "OAuth is a new authentication method that reduces friction"│ │
│  │                                                              │ │
│  │ Impact:                                                      │ │
│  │ • Tech Spec needs update (see Change #2)                    │ │
│  │ • Timeline extends +2 weeks (see Change #3)                 │ │
│  │                                                              │ │
│  │ [✓ Approve] [✗ Reject] [💬 Comment] [👁️ Preview]          │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─ Pending Change #2 ─────────────────────────────────────────┐ │
│  │ Source: PROJ-456 (Jira)                                     │ │
│  │ Artifact: Tech Spec v1.0 → v1.1                             │ │
│  │ Assigned to: @mike (Tech Lead)                              │ │
│  │ ...                                                          │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ↓ (User approves)
┌─ APPROVAL HANDLER ────────────────────────────────────────────────┐
│                                                                   │
│  1. Apply Changes                                                │
│     → Create new artifact version (PRD v2.4)                     │
│     → Update artifact status: "draft" → "approved"               │
│                                                                   │
│  2. Update Linked Entities                                       │
│     → Canvas node content updates                                │
│     → Timeline recalculates                                      │
│                                                                   │
│  3. Trigger Dependent Changes                                    │
│     → If PRD approved, notify Tech Lead of Tech Spec change      │
│     → If Timeline extends, notify stakeholders                   │
│                                                                   │
│  4. Record Audit Trail                                           │
│     → Who approved when                                          │
│     → What changed (version history)                             │
│     → Why (link to source event)                                 │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ↓
┌─ VISUALIZATION LAYER ─────────────────────────────────────────────┐
│                                                                   │
│  Canvas Updates:                                                 │
│  ┌───────────────────────────────────────────┐                   │
│  │ [PRD v2.4 Node]                          │                   │
│  │ - Shows updated content                   │                   │
│  │ - Badge: "Updated 5 min ago"              │                   │
│  │ - Link to PROJ-456                        │                   │
│  └─────────────┬─────────────────────────────┘                   │
│                │                                                  │
│                ↓                                                  │
│  ┌───────────────────────────────────────────┐                   │
│  │ [Timeline Node]                           │                   │
│  │ - Launch: Aug 15 → Aug 29                 │                   │
│  │ - Sprint 5: +2 weeks                      │                   │
│  └───────────────────────────────────────────┘                   │
│                                                                   │
│  Project Dashboard Updates:                                      │
│  - Artifacts section shows PRD v2.4                              │
│  - Timeline chart extends                                        │
│  - Activity log: "PRD updated based on PROJ-456"                 │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

## Data Model Relationships

```
Project 1─────────┬─────────* Artifact
  │               │              │
  │               │              ├─* ArtifactVersion
  │               │              └─* ChangeProposal
  │               │                      │
  │               │                      └─1 InputEvent
  │               │
  │               ├─────────* Canvas
  │               │              │
  │               │              └─* Node
  │               │
  │               ├─────────* StageTransition
  │               │
  │               └─────────* Task (from Jira, etc.)
  │
  └─* ChangeProposal ─────────* ImpactAnalysis
```

## Workflow State Transitions

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  RESEARCH   │────→│  PRD REVIEW │────→│  UX REVIEW  │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
      │ Exit Criteria:     │ Exit Criteria:     │ Exit Criteria:
      │ • Problem defined  │ • PRD approved     │ • Designs approved
      │ • Market research  │ • Stakeholder buy-in│ • Accessibility OK
      │                    │                    │
      ↓                    ↓                    ↓
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ TECH SPEC   │────→│   KICKOFF   │────→│ DEVELOPMENT │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
      │ Exit Criteria:     │ Exit Criteria:     │ Exit Criteria:
      │ • Architecture OK  │ • Team aligned     │ • Feature complete
      │ • Tech debt plan   │ • Resources alloc  │ • Code reviewed
      │                    │                    │
      ↓                    ↓                    ↓
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│     QA      │────→│   LAUNCH    │────→│    RETRO    │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Change Propagation Example

```
TRIGGER: Jira Issue "PROJ-456: Add OAuth"
  ↓
IMPACT ANALYSIS:
  ├─ PRD (High Impact)
  │  └─ New feature requirement
  │
  ├─ Tech Spec (High Impact)
  │  └─ Authentication architecture change
  │
  ├─ UX Designs (Medium Impact)
  │  └─ New OAuth selection screen
  │
  ├─ Timeline (Medium Impact)
  │  └─ +2 weeks to Sprint 5
  │
  └─ Test Plan (Low Impact)
     └─ New test cases for OAuth flow
  ↓
CHANGE PROPOSALS CREATED (5):
  ├─ ChangeProposal #1 → PRD (assigned to @sarah)
  ├─ ChangeProposal #2 → Tech Spec (assigned to @mike)
  ├─ ChangeProposal #3 → UX Designs (assigned to @emily)
  ├─ ChangeProposal #4 → Timeline (assigned to @david)
  └─ ChangeProposal #5 → Test Plan (assigned to @qa-team)
  ↓
STAKEHOLDERS REVIEW:
  ├─ @sarah approves PRD (#1) ✓
  ├─ @mike approves Tech Spec (#2) ✓
  ├─ @emily rejects UX (#3) ✗ "Want custom design"
  ├─ @david approves Timeline (#4) ✓
  └─ @qa-team auto-approved (#5) ✓ (low impact)
  ↓
CHANGES APPLIED:
  ├─ PRD v2.3 → v2.4 (merged)
  ├─ Tech Spec v1.0 → v1.1 (merged)
  ├─ UX Designs v1.2 (no change, rejected)
  ├─ Timeline updated (Sprint 5 extended)
  └─ Test Plan v1.0 → v1.1 (merged)
  ↓
CANVAS VISUALIZATION UPDATES:
  ├─ PRD node shows v2.4, badge "Updated"
  ├─ Tech Spec node shows v1.1
  ├─ UX node has alert "Changes pending review"
  ├─ Timeline chart extends by 2 weeks
  └─ All nodes linked to PROJ-456 (hover shows issue)
```

## Technology Stack

### Backend
```
FastAPI (orchestration)
├─ SQLAlchemy (ORM)
├─ Alembic (migrations)
├─ Anthropic Claude / OpenAI (AI analysis)
├─ Pinecone (vector search for impact analysis)
└─ Celery (background processing)
```

### Frontend
```
Next.js (React)
├─ TanStack Query (data fetching)
├─ shadcn/ui (components)
├─ React Flow (canvas visualization)
├─ DiffMatchPatch (diff viewer)
└─ WebSockets (real-time updates)
```

### Integrations
```
External Sources
├─ Jira (OAuth + webhooks)
├─ Zoom (OAuth + webhooks)
├─ Slack (OAuth + webhooks)
├─ GitHub (OAuth + webhooks)
├─ Confluence (OAuth)
└─ Linear (OAuth + webhooks)
```

## Scaling Considerations

### Performance
- **Background Processing**: Change analysis runs async (Celery)
- **Caching**: Redis for frequent queries
- **Vector Search**: Pinecone indexes for fast impact lookups

### Data Volume
- **Artifact Versions**: Keep last 50 versions, archive old ones
- **Change Proposals**: Auto-archive after 90 days if approved/rejected
- **Input Events**: Partition by month

### Multi-Tenancy
- **Organization Isolation**: All queries filtered by org_id
- **User Permissions**: Role-based access (PM, Tech Lead, Designer)
- **Workspace Limits**: Tier-based (free: 1 project, pro: unlimited)

---

## Next Steps: MVP Scope

### Phase 1 (Foundation) - 2 weeks
**Goal**: Basic workflow with manual change approval

- [x] Existing canvas + Jira integration
- [ ] Project model + workflow states
- [ ] Artifact model (PRD only to start)
- [ ] ChangeProposal model
- [ ] Simple staging UI

**Deliverable**: PM can manually review/approve Jira changes to PRD

### Phase 2 (AI Impact) - 2 weeks
**Goal**: Automated impact detection

- [ ] WorkflowOrchestrator service
- [ ] AI impact analyzer (Claude/GPT)
- [ ] Auto-generate change proposals
- [ ] Impact visualization

**Deliverable**: System auto-detects when Jira affects PRD

### Phase 3 (Multi-Artifact) - 2 weeks
**Goal**: Support all artifact types

- [ ] Tech Spec artifact
- [ ] Timeline artifact
- [ ] UX Design artifact
- [ ] Cross-artifact impact analysis

**Deliverable**: One Jira issue updates multiple artifacts

### Phase 4 (Stage Management) - 2 weeks
**Goal**: Full workflow stages

- [ ] Stage transition UI
- [ ] Exit criteria checklists
- [ ] Stage-specific artifacts
- [ ] Progress tracking

**Deliverable**: PM can move project through stages

### Phase 5 (Polish & Scale) - 2 weeks
**Goal**: Production-ready

- [ ] Real-time collaboration
- [ ] Notification system
- [ ] Analytics dashboard
- [ ] Performance optimization

**Deliverable**: Team uses platform for real projects

---

**Total MVP: 10 weeks**

Want me to start building Phase 1?
