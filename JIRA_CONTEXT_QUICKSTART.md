# JIRA as AI Context - Quick Start Guide

## What This Does

Your Jira tickets become **automatic knowledge** for canvas AI. When creating Problem nodes or using AI generation, the system:

1. ✅ **Searches** your Jira issues for relevant context
2. ✅ **Includes** that context in AI prompts automatically
3. ✅ **Links** related issues to canvas nodes
4. ✅ **Enriches** AI output with real data from your team's work

## Example

**Without Jira Context:**
```
User: "Create problem node about slow authentication"
AI: "Authentication performance issues can stem from..."
     [Generic response]
```

**With Jira Context:**
```
User: "Create problem node about slow authentication"
System: *Searches Jira, finds PROJ-123 and PROJ-089*
AI: "Based on PROJ-123 (Login timeout investigation) and
     PROJ-089 (Database query optimization), the authentication
     slowness stems from:
     - Missing index on users.email (see PROJ-089)
     - Connection pool exhaustion during peak hours (PROJ-123)
     - API timeout set too aggressively (PROJ-123)

     Impact: 45% of users experience >5s login times..."
     [Specific, actionable response with real data]
```

## Setup (3 steps)

### Step 1: Import Jira Issues
```bash
# Via UI: /integrations page → JIRA → Import Issues
# Enter JQL: project = MYPROJ AND status != Done

# Or via API:
POST /api/v1/integrations/jira/import
{
  "jql": "project = MYPROJ",
  "canvas_id": 5
}
```

### Step 2: Index for AI (One-time per canvas)
```bash
# Via API (auto-triggered on import):
POST /api/v1/integrations/jira/index/5

# Returns:
{
  "status": "success",
  "indexed": 12,
  "message": "Indexed 12 Jira issues for AI context"
}
```

This converts your Jira issues into searchable vectors in Pinecone.

### Step 3: Use AI with Context (Automatic!)

When generating node content, the system automatically:
1. Searches for relevant Jira issues
2. Includes them in the AI prompt
3. Links high-confidence issues to the node

## API Usage

### Search for Relevant Issues
```bash
POST /api/v1/integrations/jira/search-context
{
  "query": "authentication slow timeout",
  "canvas_id": 5,
  "top_k": 5
}

# Response:
{
  "issues": [
    {
      "score": 0.89,
      "issue_key": "PROJ-123",
      "title": "Login timeout after 30s",
      "description": "Users report slow authentication...",
      "status": "in_progress",
      "priority": "high",
      "source_url": "https://ogdenventures.atlassian.net/browse/PROJ-123",
      "task_id": 42
    },
    ...
  ],
  "formatted_context": "## Related Jira Issues\n\n### 1. PROJ-123..."
}
```

### Auto-Link Issues to Node
```bash
POST /api/v1/integrations/jira/auto-link/42?canvas_id=5&node_content=Authentication%20is%20slow

# Response:
{
  "status": "success",
  "linked_count": 2,
  "linked_task_ids": [15, 16],
  "message": "Auto-linked 2 Jira issues to node 42"
}
```

## Frontend Integration

### Add to Import Dialog
```tsx
// After importing Jira issues
const handleImport = async () => {
  const result = await integrationsApi.importFromJira({ jql, canvasId });

  // Auto-index for AI context
  await integrationsApi.indexJiraIssuesForCanvas(canvasId);

  toast({ title: 'Issues indexed for AI context!' });
};
```

### Show Related Issues in Node Editor
```tsx
// When editing a Problem node
const { data: relatedIssues } = useQuery({
  queryKey: ['jira-context', nodeContent],
  queryFn: () => integrationsApi.searchJiraContext({
    query: nodeContent,
    canvasId,
    topK: 5
  })
});

// Display in sidebar:
<div className="related-issues">
  <h4>Related Jira Issues</h4>
  {relatedIssues?.issues.map(issue => (
    <div key={issue.task_id}>
      <Badge>{issue.issue_key}</Badge>
      <span>{issue.title}</span>
      <span className="score">{(issue.score * 100).toFixed(0)}% match</span>
    </div>
  ))}
</div>
```

### AI Generation with Context
```tsx
const generateWithContext = async (userInput: string) => {
  // Search for relevant Jira context
  const contextResult = await integrationsApi.searchJiraContext({
    query: userInput,
    canvasId: currentCanvas.id,
    topK: 5
  });

  // Build enhanced prompt
  const prompt = `
    Problem: ${userInput}

    ${contextResult.formatted_context}

    Generate a detailed problem analysis...
  `;

  // Call AI with enriched prompt
  const generated = await aiApi.generateContent({ prompt, node_type: 'problem' });

  // Auto-link high-confidence issues
  const highConfidenceIssues = contextResult.issues.filter(i => i.score > 0.75);
  for (const issue of highConfidenceIssues) {
    await api.post(`/tasks/${issue.task_id}/link/${nodeId}`);
  }

  return generated;
};
```

## Configuration

Add to `.env`:
```bash
# Required for indexing (already configured)
DEFAULT_PINECONE_API_KEY=your_key
DEFAULT_PINECONE_ENVIRONMENT=us-east-1-aws
DEFAULT_PINECONE_INDEX_NAME=merlin-canvas
DEFAULT_HUGGINGFACE_API_KEY=your_key

# Optional: Auto-index on import
JIRA_AUTO_INDEX=true

# Optional: Similarity threshold for auto-linking
JIRA_AUTO_LINK_THRESHOLD=0.75
```

## How It Works Under the Hood

### 1. Indexing Phase
```
Jira Issue: PROJ-123
├─ Title: "Login timeout after 30s"
├─ Description: "Users report authentication taking too long..."
├─ Status: in_progress
└─ Priority: high

↓ [Generate Embedding]

Vector: [0.23, 0.87, 0.45, ..., 0.91] (1024 dimensions)

↓ [Store in Pinecone]

Namespace: org_4
Vector ID: jira_42
Metadata: {
  type: "jira_issue",
  issue_key: "PROJ-123",
  canvas_id: 5,
  task_id: 42,
  ...
}
```

### 2. Search Phase
```
User Input: "Authentication is slow"

↓ [Generate Embedding]

Query Vector: [0.21, 0.89, 0.43, ..., 0.88]

↓ [Search Pinecone]

Find similar vectors (cosine similarity)

↓ [Return Matches]

Results:
- PROJ-123 (0.89 similarity)
- PROJ-089 (0.82 similarity)
```

### 3. AI Generation Phase
```
AI Prompt:
"""
Problem: Authentication is slow

## Related Jira Issues

### 1. PROJ-123: Login timeout after 30s
**Status**: in_progress | **Priority**: high
**Description**: Users report slow authentication response times...
[View in Jira](https://...)

### 2. PROJ-089: Database query taking 5+ seconds
**Status**: pending | **Priority**: medium
**Description**: Query optimization needed for user table...

## Instructions
Generate a detailed problem analysis considering the Jira issues above...
"""

↓ [AI processes with context]

Output: Detailed analysis referencing PROJ-123 and PROJ-089
```

### 4. Auto-Linking Phase
```
Generated Content + Jira Issues (score > 0.75)

↓ [Create Links]

task_node_links table:
- task_id=42 (PROJ-123) ← → node_id=15 (Problem node)
- task_id=45 (PROJ-089) ← → node_id=15

↓ [UI Shows]

Problem Node displays:
🎫 Linked Issues: PROJ-123, PROJ-089
```

## Benefits

### Before: Manual Context Gathering
1. Create Problem node
2. Remember relevant Jira ticket
3. Open Jira, search, find ticket
4. Copy details
5. Paste into canvas
6. Manually link ticket
7. Repeat for each related issue

**Time: ~10 minutes per node**

### After: Automatic Context
1. Create Problem node
2. Type brief description
3. Click "Generate with AI"
4. ✨ AI already has all relevant Jira context
5. ✨ Issues auto-linked
6. Done!

**Time: ~30 seconds per node**

---

## Troubleshooting

### "No results found"
- Ensure issues are indexed: `POST /jira/index/{canvas_id}`
- Check Pinecone configuration in `.env`
- Verify HuggingFace API key is valid

### "Low similarity scores"
- Issues may not be related to query text
- Try more specific search queries
- Lower the `threshold` parameter (default 0.75)

### "Failed to index"
- Check Pinecone API key and index name
- Ensure HuggingFace API key is valid
- Verify canvas has Jira tasks with `source=jira`

---

## Next Steps

1. ✅ **Backend Complete** - Service + API endpoints ready
2. 🔧 **Frontend Integration** - Add to import dialog and node editor
3. 🎨 **Template Enhancement** - Auto-inject context into AI prompts
4. 🧪 **Testing** - Import real Jira data and test search quality

Want me to implement the frontend components next?
