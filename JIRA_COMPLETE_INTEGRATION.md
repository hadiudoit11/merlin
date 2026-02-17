# Complete Jira Integration - User & System Guide

## Overview

This is the **complete Jira integration system** with three key capabilities:

1. **🎯 Automatic Discovery** - AI-powered semantic search finds related Jira issues
2. **✏️ Manual Management** - Full UI for customizing connections
3. **🤖 MCP Protocol** - Claude Desktop can access Jira context

---

## Part 1: Automatic Discovery (Strategic Context Panel)

### What It Does
When you select a canvas node, automatically finds **all related Jira issues** using semantic similarity.

### How to Use

```typescript
// Strategic Context Panel appears in right sidebar
<StrategicContextPanel
  nodeId={selectedNode.id}
  nodeContent={selectedNode.content}  // "Authentication is slow"
  canvasId={5}
/>

// Automatically searches and shows:
// ✅ PROJ-123 (91% match) - Login timeout
// ✅ PROJ-089 (85% match) - DB query slow
// ⚠️  PROJ-045 (72% match) - API optimization
```

### Features
- **Real-time search** - Updates as you edit node content
- **Confidence scores** - 0-100% relevance
- **Status indicators** - Pending, In Progress, Completed
- **One-click linking** - Add issues to node
- **Filters** - All, High Match, Unlinked

### Location
- **Component**: `/Merlin-fe/src/components/canvas/StrategicContextPanel.tsx`
- **Guide**: `/Merlin/PM_STRATEGIC_CONTEXT_GUIDE.md`

---

## Part 2: Manual Connection Management

### What It Does
Full UI dialog to manually add/remove Jira issues from canvases or nodes. **System auto-discovers, user refines.**

### How to Use

```typescript
// Open from canvas toolbar or node menu
<JiraConnectionsManager
  open={showDialog}
  onOpenChange={setShowDialog}
  canvasId={5}
  nodeId={42}  // Optional - manage connections for specific node
  nodeTitle="Authentication Performance"
/>
```

### UI Features

**3 Tabs:**

1. **Linked** - Issues currently connected
   - Shows all linked issues
   - One-click unlink
   - Status and assignee visible

2. **Available** - All Jira issues you can link
   - Search by key/title/description
   - Select multiple (checkbox)
   - Bulk link action
   - Shows which are already linked elsewhere

3. **Suggested** - AI-powered recommendations
   - Only appears when searching
   - Uses semantic similarity
   - Shows confidence scores
   - Mix of auto-suggestions + manual search

### Example Flow

```
User clicks "Manage Connections" on Problem node
      ↓
Dialog opens showing:
- Linked: 3 issues already connected
- Available: 47 other Jira issues on canvas
- Search: User types "authentication"
      ↓
Suggested tab appears with AI matches:
- PROJ-123 (91% match) ← Auto-suggested
- PROJ-089 (85% match) ← Auto-suggested
- PROJ-234 (68% match)
      ↓
User reviews:
✓ Keeps PROJ-123 and PROJ-089 (already linked)
✓ Links PROJ-234 manually
✗ Unlinks PROJ-456 (not relevant)
      ↓
Saves → Node now shows "🎫 4 linked issues"
```

### Triggering the Dialog

**Option 1: From Canvas Toolbar**
```tsx
<Button onClick={() => setShowConnectionManager(true)}>
  <Link2 className="h-4 w-4 mr-2" />
  Manage Jira Connections
</Button>
```

**Option 2: From Node Context Menu**
```tsx
<ContextMenuItem onClick={() => {
  setSelectedNode(node);
  setShowConnectionManager(true);
}}>
  <Link2 className="h-4 w-4 mr-2" />
  Link Jira Issues
</ContextMenuItem>
```

**Option 3: From Strategic Context Panel**
```tsx
// Add button to panel header
<Button
  variant="outline"
  onClick={() => setShowConnectionManager(true)}
>
  <Settings className="h-4 w-4 mr-2" />
  Customize Connections
</Button>
```

### Location
- **Component**: `/Merlin-fe/src/components/canvas/JiraConnectionsManager.tsx`
- **Integration**: Add to canvas page layout

---

## Part 3: MCP Protocol Integration

### What It Does
Exposes Jira strategic context to **Claude Desktop** via MCP protocol. Claude can search for related Jira issues when helping you with canvas work.

### MCP Tools Available

#### 1. `search_jira_context`
**Purpose**: Search for Jira issues related to a topic

```python
# Claude Desktop can call:
search_jira_context({
  "query": "authentication performance issues",
  "canvas_id": 5,
  "top_k": 10
})

# Returns:
{
  "results_count": 8,
  "issues": [
    {
      "issue_key": "PROJ-123",
      "title": "Login timeout after 30s",
      "confidence_score": 91.2,
      "status": "in_progress",
      ...
    }
  ]
}
```

#### 2. `get_jira_connections`
**Purpose**: Get all Jira issues connected to a canvas/node

```python
# Get issues for entire canvas
get_jira_connections({
  "canvas_id": 5
})

# Get issues for specific node
get_jira_connections({
  "canvas_id": 5,
  "node_id": 42
})

# Returns:
{
  "connected_issues_count": 5,
  "issues": [...]
}
```

#### 3. `index_jira_for_canvas`
**Purpose**: Index Jira issues for semantic search

```python
index_jira_for_canvas({
  "canvas_id": 5
})

# Returns:
{
  "indexed_count": 12,
  "status": "success"
}
```

### Example Claude Desktop Conversation

```
You: Help me understand the authentication performance issues on canvas 5

Claude: Let me search for related Jira issues...
        [Calls search_jira_context with query="authentication performance"]

Claude: I found 8 related Jira issues:

        1. PROJ-123 (91% match) - Login timeout after 30s
           Status: In Progress
           This is the primary issue. The team is investigating database query
           performance and connection pooling.

        2. PROJ-089 (85% match) - Database query optimization
           Status: Pending
           This is a blocker for PROJ-123. The users table is missing an index.

        3. PROJ-045 (72% match) - API response time
           Status: Completed
           This was previously resolved but might be related.

        Based on these issues, the authentication slowness stems from two root causes:
        1. Missing database index (PROJ-089)
        2. Connection pool exhaustion during peak hours (PROJ-123)

        Would you like me to help create a Problem node that references these issues?
```

### Setup for MCP

**1. Configure MCP Server**

Set environment variables:
```bash
export MCP_USER_ID=3
export MCP_ENABLE_AUDIT=true
```

**2. Run MCP Server**

```bash
cd /path/to/Merlin
python mcp_server.py
```

**3. Connect Claude Desktop**

Add to Claude Desktop MCP config (`~/Library/Application Support/Claude/config.json`):

```json
{
  "mcpServers": {
    "merlin-canvas": {
      "command": "python",
      "args": ["/path/to/Merlin/mcp_server.py"],
      "env": {
        "MCP_USER_ID": "3",
        "MCP_ENABLE_AUDIT": "true"
      }
    }
  }
}
```

**4. Restart Claude Desktop**

The Jira tools will now be available to Claude.

### MCP Code Location
- **Server**: `/Merlin/mcp_server.py`
- **Service**: `/Merlin/app/services/jira_context_service.py`

---

## Complete Integration Example

### Scenario: PM Planning Authentication Work

**Step 1: Import Jira Issues** (One-time)
```typescript
// User goes to /integrations → Jira → Import Issues
// Enters JQL: project = AUTH AND status != Done
// Imports 23 issues

// System automatically indexes them for search
await integrationsApi.indexJiraIssuesForCanvas(canvasId);
```

**Step 2: Create Problem Node**
```typescript
// User creates Problem node: "Authentication is slow"
// Strategic Context Panel auto-appears with related issues:
// - PROJ-123 (91% match)
// - PROJ-089 (85% match)
// - PROJ-045 (72% match)
```

**Step 3: Review Auto-Discovered Issues**
```typescript
// User clicks "Manage Connections" to customize
<JiraConnectionsManager />

// Dialog shows:
// Linked: (empty - nothing linked yet)
// Suggested: 3 issues from auto-discovery
//
// User reviews each:
// ✓ Links PROJ-123 and PROJ-089 (high confidence)
// ✗ Skips PROJ-045 (not directly related)
```

**Step 4: Manual Search for More Context**
```typescript
// User searches: "database performance"
// Suggested tab shows 5 more issues:
// - PROJ-234 (78% match) - Connection pooling
// - PROJ-567 (65% match) - Query optimization
//
// User links PROJ-234
```

**Step 5: Claude Desktop Gets Context**
```
User opens Claude Desktop:

You: Help me document the authentication problem

Claude: [Calls get_jira_connections for this node]

Claude: Based on the 3 linked Jira issues (PROJ-123, PROJ-089, PROJ-234),
        here's a structured problem document:

        ## Authentication Performance Problem

        ### Root Causes
        1. Missing database index on users.email (PROJ-089)
        2. Connection pool exhaustion during peak load (PROJ-234)
        3. Session timeout set too aggressively (PROJ-123)

        ### Impact
        - 45% of users experience >5s login times (per PROJ-123)
        - Peak hour failures increased 300% (PROJ-234)

        ### Work In Progress
        - PROJ-123: Investigation ongoing (In Progress)
        - PROJ-089: Pending implementation (Pending)
        - PROJ-234: Architecture review (Pending)

        Would you like me to create Key Results for this initiative?
```

**Step 6: Updates Flow Back**
```typescript
// When Jira status changes (via webhook):
// PROJ-089 moves to "In Progress"
//
// Strategic Context Panel updates automatically
// Node badge updates: "🎫 3 linked (1 completed, 2 in progress)"
```

---

## Architecture

### Data Flow

```
┌─ Jira (External) ────────────────────────────────────┐
│ PROJ-123, PROJ-089, PROJ-234...                      │
└────────────┬──────────────────────────────────────────┘
             │
             │ Import (OAuth)
             ↓
┌─ Backend Database ───────────────────────────────────┐
│ tasks table (source=jira)                            │
│ - id, source_id (PROJ-123), title, description...    │
│                                                       │
│ task_node_links table                                │
│ - task_id ↔ node_id (many-to-many)                   │
└────────────┬──────────────────────────────────────────┘
             │
             │ Indexing
             ↓
┌─ Pinecone (Vector Store) ────────────────────────────┐
│ Namespace: org_4                                     │
│ Vectors:                                             │
│ - jira_42 → [0.23, 0.87, ...] (embedding of PROJ-123)│
│   metadata: {issue_key, canvas_id, ...}              │
└────────────┬──────────────────────────────────────────┘
             │
             │ Search Query
             ↓
┌─ Strategic Context Panel (Frontend) ─────────────────┐
│ User types: "authentication slow"                    │
│   ↓ Generate embedding                               │
│   ↓ Query Pinecone                                   │
│   ↓ Rank by similarity                               │
│ Shows: PROJ-123 (91%), PROJ-089 (85%)                │
└────────────┬──────────────────────────────────────────┘
             │
             │ MCP Protocol
             ↓
┌─ Claude Desktop ─────────────────────────────────────┐
│ Claude calls: search_jira_context()                  │
│ Gets same results + formatted context                │
│ Uses in conversation with user                       │
└───────────────────────────────────────────────────────┘
```

### Key Tables

**tasks** (Jira issues stored as tasks)
```sql
id | source | source_id | title              | canvas_id | ...
---|--------|-----------|-------------------|-----------|----
42 | jira   | PROJ-123  | Login timeout...  | 5         | ...
43 | jira   | PROJ-089  | DB query slow...  | 5         | ...
```

**task_node_links** (Manual connections)
```sql
task_id | node_id | link_type
--------|---------|----------
42      | 15      | related
43      | 15      | related
```

**Pinecone Index** (Semantic search)
```json
{
  "namespace": "org_4",
  "vectors": [
    {
      "id": "jira_42",
      "values": [0.23, 0.87, ...],
      "metadata": {
        "type": "jira_issue",
        "issue_key": "PROJ-123",
        "canvas_id": 5,
        "task_id": 42
      }
    }
  ]
}
```

---

## API Endpoints Summary

### Jira Strategic Context
- `POST /jira/index/{canvas_id}` - Index issues for search
- `POST /jira/search-context` - Semantic search
- `POST /jira/auto-link/{node_id}` - Auto-link high-confidence issues

### Task Management
- `GET /tasks?canvas_id=5&source=jira` - Get all Jira tasks on canvas
- `GET /tasks?linked_node_id=42` - Get tasks linked to node
- `POST /tasks/{task_id}/link/{node_id}` - Link task to node
- `DELETE /tasks/{task_id}/link/{node_id}` - Unlink task from node
- `PUT /tasks/{task_id}` - Update task (including canvas_id)

### Jira Integration (Existing)
- `POST /jira/import` - Import issues from Jira
- `GET /jira/status` - Connection status
- `POST /jira/webhook` - Receive updates from Jira

---

## Setup Checklist

### Backend
- [x] `JiraContextService` implemented
- [x] API endpoints added to `/jira.py`
- [x] MCP tools added to `mcp_server.py`
- [x] Backend restarted

### Frontend
- [x] `StrategicContextPanel` component created
- [x] `JiraConnectionsManager` component created
- [x] API methods added to `integrations-api.ts`
- [ ] Add panels to canvas page layout
- [ ] Add "Manage Connections" buttons

### MCP
- [ ] Set `MCP_USER_ID` environment variable
- [ ] Configure Claude Desktop MCP settings
- [ ] Restart Claude Desktop
- [ ] Test MCP tools

### Configuration
- [ ] Ensure Pinecone is configured (`.env`)
- [ ] Ensure HuggingFace API key is set
- [ ] Test indexing with real Jira data

---

## Next Steps

**Ready to integrate:**

1. **Add Strategic Context Panel to canvas** (5 minutes)
   - See `STRATEGIC_CONTEXT_INTEGRATION.md`

2. **Add Connection Manager button** (2 minutes)
   - Add to canvas toolbar or node menu

3. **Configure MCP** (5 minutes)
   - Set env vars, update Claude Desktop config

4. **Test end-to-end** (15 minutes)
   - Import Jira issues → Index → Search → Link → Use in Claude Desktop

**Want me to integrate any of these now?**
