# Quick Start - Product Development Platform Phase 1

## What You Can Do Now

Phase 1 is complete! Here's what's working and how to use it.

## 1. Create a Product Development Project

```bash
curl -X POST http://localhost:8000/api/v1/projects/ \
  -H "Authorization: Bearer $YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Mobile App Redesign",
    "description": "Redesign the mobile app with new UX and dark mode",
    "organization_id": 4,
    "canvas_id": 5,
    "current_stage": "research",
    "status": "planning",
    "planned_launch_date": "2026-06-01T00:00:00",
    "exit_criteria": [
      {
        "item": "User research completed",
        "completed": false
      },
      {
        "item": "Competitive analysis done",
        "completed": false
      }
    ]
  }'
```

**Response:**
```json
{
  "id": 1,
  "name": "Mobile App Redesign",
  "description": "Redesign the mobile app with new UX and dark mode",
  "current_stage": "research",
  "status": "planning",
  "canvas_id": 5,
  "organization_id": 4,
  "created_by_id": 3,
  "planned_start_date": null,
  "planned_launch_date": "2026-06-01T00:00:00",
  "actual_launch_date": null,
  "settings": {},
  "exit_criteria": [
    {"item": "User research completed", "completed": false},
    {"item": "Competitive analysis done", "completed": false}
  ],
  "created_at": "2026-02-16T23:30:00",
  "updated_at": "2026-02-16T23:30:00"
}
```

## 2. List All Projects

```bash
# All projects in your organizations
curl http://localhost:8000/api/v1/projects/ \
  -H "Authorization: Bearer $YOUR_TOKEN"

# Projects on specific canvas
curl "http://localhost:8000/api/v1/projects/?canvas_id=5" \
  -H "Authorization: Bearer $YOUR_TOKEN"

# Projects in specific stage
curl "http://localhost:8000/api/v1/projects/?current_stage=development" \
  -H "Authorization: Bearer $YOUR_TOKEN"
```

## 3. Get Project with Full Details

```bash
curl http://localhost:8000/api/v1/projects/1/details \
  -H "Authorization: Bearer $YOUR_TOKEN"
```

**Response includes:**
- Project info
- All artifacts (PRDs, Tech Specs, etc.)
- Pending change proposals
- Recent stage transitions

## 4. Move Project to Next Stage

```bash
curl -X POST http://localhost:8000/api/v1/projects/1/transitions \
  -H "Authorization: Bearer $YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 1,
    "from_stage": "research",
    "to_stage": "prd_review",
    "notes": "Research completed. Ready to draft PRD.",
    "exit_criteria_snapshot": [
      {"item": "User research completed", "completed": true},
      {"item": "Competitive analysis done", "completed": true}
    ],
    "all_criteria_met": true
  }'
```

This automatically updates the project's `current_stage` to `prd_review`.

## 5. Update Project

```bash
curl -X PUT http://localhost:8000/api/v1/projects/1 \
  -H "Authorization: Bearer $YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "active",
    "planned_launch_date": "2026-07-15T00:00:00"
  }'
```

## Workflow Stages

Projects go through these stages:

1. **research** - Market research, user research, problem definition
2. **prd_review** - Product Requirements Document review and approval
3. **ux_review** - UX/Design review and approval
4. **tech_spec** - Technical specification and architecture
5. **project_kickoff** - Team alignment and resource allocation
6. **development** - Implementation
7. **qa** - Quality assurance and testing
8. **launch** - Production deployment
9. **retrospective** - Post-launch review and lessons learned

## Example: Full Project Lifecycle

```bash
# 1. Create project
PROJECT_ID=$(curl -X POST http://localhost:8000/api/v1/projects/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "OAuth Login Feature",
    "organization_id": 4,
    "canvas_id": 5,
    "current_stage": "research"
  }' | jq -r '.id')

# 2. Research phase completed
curl -X POST http://localhost:8000/api/v1/projects/$PROJECT_ID/transitions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": '$PROJECT_ID',
    "from_stage": "research",
    "to_stage": "prd_review",
    "all_criteria_met": true
  }'

# 3. Update project status
curl -X PUT http://localhost:8000/api/v1/projects/$PROJECT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "active"}'

# 4. Check current state
curl http://localhost:8000/api/v1/projects/$PROJECT_ID/details \
  -H "Authorization: Bearer $TOKEN" | jq '{name, current_stage, status}'
```

## What's Coming in Phase 2

Once Phase 2 is complete, you'll be able to:

1. **Create Artifacts**
   ```bash
   # Create PRD artifact
   POST /api/v1/artifacts/
   {
     "project_id": 1,
     "name": "Mobile App PRD",
     "artifact_type": "prd",
     "content": "# Product Requirements Document..."
   }
   ```

2. **Generate Change Proposals**
   - System detects new Jira issue
   - AI analyzes impact on PRD, Tech Spec, Timeline
   - Creates ChangeProposals automatically
   - Assigns to stakeholders for review

3. **Approve/Reject Changes**
   ```bash
   # Approve change
   POST /api/v1/change-proposals/123/approve

   # Reject change
   POST /api/v1/change-proposals/124/reject
   {
     "review_notes": "Scope too large, needs refinement"
   }
   ```

## Database Schema Reference

### Projects
- **Primary workflow container**
- Links to Canvas for visualization
- Tracks current stage and status
- Contains exit criteria checklist

### Stage Transitions
- **Audit trail** of workflow progression
- Records who moved to next stage
- Captures exit criteria snapshot
- Timestamped for compliance

### Artifacts (Phase 2)
- PRDs, Tech Specs, UX Designs, Timelines
- Version controlled
- Linked to Project
- Can be visualized as nodes on canvas

### Change Proposals (Phase 2)
- Proposed changes to artifacts
- Triggered by external events (Jira, Zoom, etc.)
- AI-generated with impact analysis
- Requires stakeholder approval

## Testing the API

You can use the interactive API docs at:
```
http://localhost:8000/docs
```

This provides:
- All endpoint documentation
- Try-it-out functionality
- Request/response schemas
- Authentication testing

## Next Steps

1. **Test the Project API** - Create a few test projects
2. **Try Stage Transitions** - Move projects through workflow
3. **Prepare for Phase 2** - Think about which artifacts you want to create
4. **Explore the Vision** - Read `PRODUCT_DEVELOPMENT_PLATFORM_VISION.md` for the full picture

---

**Questions?** See:
- `PHASE_1_COMPLETE.md` - What we built
- `ARCHITECTURE_OVERVIEW.md` - Full system design
- `PRODUCT_DEVELOPMENT_PLATFORM_VISION.md` - Complete vision
