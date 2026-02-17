# PM Strategic Context - User Guide

## Overview

As a PM, when working on a problem or objective, you need to see **all related Jira work** to make informed strategic decisions. The Strategic Context Panel gives you automatic discovery of:

- Related tickets you didn't know existed
- Work already in progress that touches your area
- Historical context from similar problems
- Dependencies and blockers
- Team member ownership

## Visual Overview

```
┌─ Canvas ────────────────────────────┬─ Strategic Context Panel ─────────┐
│                                     │                                   │
│  ┌─ Problem Node ────────────┐     │  🔍 Search: "auth slow"           │
│  │ Authentication is slow    │     │  ───────────────────────────────   │
│  │                           │     │  📊 15 found • 3 linked • 12 new  │
│  │ Users report 5+ second    │     │                                   │
│  │ login times during peak   │◄────┤  ┌─ Strong Match (5) ──────────┐ │
│  │ hours...                  │     │  │                              │ │
│  └───────────────────────────┘     │  │ 🎫 PROJ-123  [91% match]    │ │
│                                     │  │ Login timeout after 30s     │ │
│  ┌─ Key Result ──────────────┐     │  │ Status: In Progress         │ │
│  │ Reduce login time to <1s  │     │  │ 🔗 Link  🔗 Linked          │ │
│  └───────────────────────────┘     │  │                              │ │
│                                     │  │ 🎫 PROJ-089  [85% match]    │ │
│                                     │  │ DB query optimization       │ │
│                                     │  │ Status: Pending             │ │
│                                     │  │ 🔗 Link  🌐 View            │ │
│                                     │  └──────────────────────────────┘ │
│                                     │                                   │
│                                     │  ┌─ Moderate Match (7) ────────┐ │
│                                     │  │ 🎫 PROJ-045  [72% match]    │ │
│                                     │  │ API response optimization   │ │
│                                     │  └──────────────────────────────┘ │
└─────────────────────────────────────┴───────────────────────────────────┘
```

## How to Use

### 1. Open Strategic Context Panel

**In Canvas View:**
- Right sidebar → "Strategic Context" tab
- Or: Click "🔍 Find Related Work" button on any Problem/Objective node

### 2. Automatic Discovery

**When you select a node**, the panel automatically:
1. Analyzes the node content
2. Searches all Jira issues on the canvas
3. Ranks them by relevance (0-100% match)
4. Shows them grouped by confidence

**Example:**
```
You select Problem Node: "Authentication is slow and timing out"

Panel discovers:
✅ PROJ-123: Login timeout (91% match) - IN PROGRESS
✅ PROJ-089: Database query slow (85% match) - PENDING
⚠️  PROJ-045: API optimization (72% match) - PLANNED
⚠️  PROJ-012: Session management (68% match) - COMPLETED
```

### 3. Manual Search

**Custom searches** for broader context:
- Search: "performance" → Find all performance-related tickets
- Search: "database" → Find all DB-related work
- Search: "John's tickets" → Find work by specific person

### 4. Review Confidence Levels

**Strong Match (80-100%)**
- Direct relationship to your problem
- Should definitely be linked
- Often blockers or dependencies

**Moderate Match (60-79%)**
- Related but tangential
- May provide useful context
- Consider linking if relevant

**Weak Match (< 60%)**
- Peripheral relationship
- Might be false positives
- Review to be sure you're not missing anything

### 5. Link Issues to Nodes

**One-click linking:**
1. Click "🔗 Link" button on any issue
2. Issue becomes linked to your current node
3. Badge changes to "✅ Linked"
4. Node shows "🎫 3 linked issues" count

**Why link?**
- Traceability: See what Jira work supports this objective
- Context: Teammates see the full picture
- Updates: Changes in Jira reflect on canvas
- Metrics: Track progress across tools

### 6. Filter View

**Use filters to focus:**
- **All** - Show everything
- **High Match** - Only 75%+ matches
- **Unlinked** - Only issues not yet linked

### 7. View by Confidence or Chronology

**Tabs:**
- **By Confidence** - Grouped: Strong → Moderate → Weak
- **All Issues** - Chronological list with confidence badges

---

## PM Workflows

### Workflow 1: Problem Discovery

**Scenario:** You're creating a new Problem node

1. **Add Problem node**: "Users complain about slow checkout"
2. **Panel auto-searches** for related Jira issues
3. **Discover:**
   - `CART-456`: Payment gateway timeout (88% match)
   - `CART-234`: Database connection pool (76% match)
   - `SHIP-123`: Address validation slow (71% match)
4. **Insight:** The problem isn't just checkout - it's three subsystems!
5. **Link all three** to the Problem node
6. **Create sub-problems** for each subsystem
7. **Link specific issues** to specific sub-problems

**Result:** You now have a complete map of all related work, preventing duplicate efforts and surfacing hidden blockers.

---

### Workflow 2: Objective Planning

**Scenario:** Planning Q2 objectives

1. **Create Objective**: "Improve platform performance"
2. **Search**: "performance optimization speed slow"
3. **Panel shows:**
   - 12 in-progress performance tickets
   - 8 completed optimization work
   - 5 planned improvements
4. **Review completed work** to understand what's been tried
5. **Check in-progress work** to avoid duplication
6. **Link planned work** to your objective
7. **Identify gaps** - what's not covered by existing tickets?

**Result:** Data-driven objective scoping with full visibility into team capacity.

---

### Workflow 3: Blocker Analysis

**Scenario:** Key Result is blocked

1. **Select blocked Key Result node**
2. **Panel shows related issues**
3. **Filter to "High Match"**
4. **Check statuses:**
   - `PROJ-123`: In Progress (blocking you)
   - `PROJ-089`: Pending (needs prioritization)
5. **Open Jira** (click 🌐) to check progress
6. **Update canvas** with blocker context
7. **Escalate** if needed

**Result:** Quick blocker identification and resolution path.

---

### Workflow 4: Context for New Team Members

**Scenario:** Onboarding PM to existing initiative

1. **Open canvas** with problem/objective
2. **Panel shows** all related Jira work
3. **New PM sees:**
   - Historical context (completed tickets)
   - Current work (in-progress tickets)
   - Future plans (planned tickets)
   - Who's working on what (assignees)
4. **Links** tell the full story
5. **Confidence scores** show what's most relevant

**Result:** Instant context without hours of digging through Jira.

---

### Workflow 5: Sprint Planning Support

**Scenario:** Determining sprint capacity

1. **Search**: "authentication" (your sprint theme)
2. **Panel discovers** 18 related tickets
3. **Check statuses:**
   - 3 In Progress (carry-over)
   - 5 Pending (candidate for sprint)
   - 10 Backlog (future)
4. **Check assignees** to see team capacity
5. **Link high-priority** issues to your Objective
6. **Share canvas** with team for sprint planning

**Result:** Visual sprint plan with full context.

---

## Reading the Panel

### Issue Card Anatomy

```
┌───────────────────────────────────────────┐
│ 🎫 PROJ-123    [High Priority]  [91% ✓]  │ ← Issue key, priority, confidence
│ ─────────────────────────────────────────  │
│ Login timeout after 30 seconds            │ ← Title
│ ─────────────────────────────────────────  │
│ Users report authentication response      │ ← Description preview
│ times exceeding 30s during peak hours...  │
│ ─────────────────────────────────────────  │
│ 🕒 In Progress • @john-doe                │ ← Status, assignee
│                         🔗 Link  🌐 View  │ ← Actions
└───────────────────────────────────────────┘
```

### Confidence Score Meaning

| Score | Meaning | Action |
|-------|---------|--------|
| 90-100% | Exact match | **Link immediately** - Core to your problem |
| 80-89% | Strong relation | **Link** - Direct dependency or blocker |
| 70-79% | Related work | **Review** - Might provide useful context |
| 60-69% | Tangential | **Consider** - Peripheral but may be relevant |
| <60% | Weak match | **Skim** - Likely not relevant, but check |

### Status Icons

| Icon | Status | Meaning |
|------|--------|---------|
| 🕒 | Pending | Not started yet |
| 📈 | In Progress | Active work |
| ✅ | Completed | Done |
| ❌ | Cancelled | Won't do |

---

## Best Practices

### ✅ Do This

1. **Index after import** - Run index immediately after importing Jira issues
2. **Search broadly** - Cast a wide net, then filter down
3. **Link high-confidence issues** - Anything 75%+ is usually relevant
4. **Review linked issues regularly** - Keep connections current
5. **Use filters** - "Unlinked" filter helps find missed connections

### ❌ Avoid This

1. **Don't skip indexing** - Panel won't work without it
2. **Don't link everything** - Only link truly relevant issues (>70%)
3. **Don't ignore low scores** - Sometimes a 65% match is actually important
4. **Don't link and forget** - Update links as work evolves

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + K` | Focus search |
| `Cmd/Ctrl + L` | Link highlighted issue |
| `↑↓` | Navigate issues |
| `Enter` | Open issue in Jira |
| `Esc` | Clear search |

---

## Configuration

### Enable for Your Canvas

1. Go to Canvas Settings
2. Toggle "Enable Jira Strategic Context"
3. Import Jira issues: `/integrations` → Jira → Import
4. Index them: Click "Index for Context" (automatic on import)

### Set Auto-Link Threshold

**Settings → Integrations → Jira:**
- **Auto-link threshold**: 75% (default)
- Issues above this threshold auto-link when generated

### Configure Search Depth

**Settings → Integrations → Jira:**
- **Max context issues**: 15 (default)
- Higher = more comprehensive, slower search

---

## Troubleshooting

### "No results found"

**Causes:**
- Issues not indexed yet
- Search query too specific
- No Jira issues on this canvas

**Fix:**
1. Check canvas has Jira issues: Task view → Filter "Source: Jira"
2. Re-index: Canvas settings → "Re-index Jira Issues"
3. Try broader search terms

### "Confidence scores seem wrong"

**Causes:**
- Issue descriptions are sparse
- Search uses different terminology
- Vector index needs refresh

**Fix:**
1. Add more detail to Jira issues
2. Try synonyms in search
3. Re-index after updating Jira

### "Takes too long to search"

**Causes:**
- Large number of indexed issues
- Slow Pinecone response

**Fix:**
1. Reduce "Max context issues" in settings
2. Filter to current canvas only
3. Check Pinecone status

---

## API for Automation

### Auto-link on Node Creation

```typescript
// After creating a Problem node
const result = await integrationsApi.autoLinkJiraIssuesToNode(
  problemNode.id,
  canvasId,
  problemNode.content,
  { threshold: 0.75, maxLinks: 5 }
);

console.log(`Auto-linked ${result.linked_count} issues`);
```

### Batch Index All Canvases

```typescript
// For admins: Index all canvases at once
for (const canvas of canvases) {
  await integrationsApi.indexJiraIssuesForCanvas(canvas.id);
}
```

### Custom Search Queries

```typescript
// Search for specific scenarios
const authIssues = await integrationsApi.searchJiraContext({
  query: 'authentication security login',
  canvasId: 5,
  topK: 20
});

const performanceIssues = await integrationsApi.searchJiraContext({
  query: 'slow performance optimization',
  canvasId: 5,
  topK: 20
});
```

---

## Example: Complete PM Session

**Scenario:** Planning authentication overhaul

### 1. Setup (2 minutes)
- Import Jira issues: `project = AUTH AND updated >= -90d`
- Index: 47 issues indexed
- Open Strategic Context panel

### 2. Discovery (5 minutes)
- Search: "authentication security"
- Find: 23 related issues
- Filter: High Match → 8 issues
- Review each, open in Jira as needed

### 3. Grouping (10 minutes)
- Create Problem nodes for clusters:
  - "Login Performance" → Link PROJ-123, PROJ-089
  - "Session Management" → Link PROJ-234, PROJ-456
  - "OAuth Integration" → Link PROJ-567, PROJ-678
  - "Security Hardening" → Link PROJ-789, PROJ-890

### 4. Planning (15 minutes)
- For each Problem, check issue statuses
- Identify what's done, in progress, planned
- Create Key Results based on pending work
- Link Key Results to in-progress issues
- Identify gaps (no Jira ticket yet)

### 5. Communicate (5 minutes)
- Share canvas with team
- Canvas shows complete picture:
  - 23 Jira issues across 4 problem areas
  - Clear ownership (assignees visible)
  - Status at a glance
  - Strategic grouping

**Total time:** 37 minutes for complete strategic overview

**Without tool:** 2-3 hours of Jira searching, spreadsheet creation, and manual linking

---

## Success Metrics

Track the impact:
- **Time saved**: Before/after for problem analysis
- **Issues discovered**: Average issues found per search
- **Link accuracy**: % of high-confidence links that stay linked
- **Team alignment**: Reduced "I didn't know about that ticket" moments

---

Ready to use? **See `StrategicContextPanel.tsx` for implementation.**
