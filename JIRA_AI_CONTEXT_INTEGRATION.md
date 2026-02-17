# JIRA as AI Context - Implementation Guide

## Overview

This implementation uses **Jira issues as a knowledge base** for AI-powered canvas generation. When creating/editing nodes (especially Problems), the system:

1. **Indexes** Jira issues into the vector store (Pinecone)
2. **Searches** for relevant issues based on node content
3. **Includes** issue context in AI prompts automatically
4. **Auto-links** relevant issues to nodes

## How It Works

```
User creates Problem node: "Authentication is slow"
              ↓
    Semantic search in vector store
              ↓
    Find relevant Jira issues:
    - PROJ-123: Login timeout issues (0.89 similarity)
    - PROJ-124: Database query performance (0.82 similarity)
              ↓
    Include in AI prompt context:
    "Based on these related Jira issues: [PROJ-123]..."
              ↓
    AI generates enriched Problem description
              ↓
    Auto-link PROJ-123 and PROJ-124 to the node
```

## Architecture

### 1. Index Jira Issues (One-Time Setup)

After importing Jira issues to a canvas, index them:

```python
# app/services/jira_context_service.py
async def index_jira_issues(canvas_id, user_id, org_id):
    """
    Converts Jira issues into searchable embeddings.

    Indexes:
    - Issue key (PROJ-123)
    - Title
    - Description
    - Comments/context
    - Metadata (status, priority, assignee)
    """
```

**Vector Store Structure:**
```
Namespace: org_4 or user_3
Vectors:
  jira_1 → [0.23, 0.87, ...] → metadata: {type: "jira_issue", issue_key: "PROJ-123", ...}
  jira_2 → [0.45, 0.12, ...] → metadata: {type: "jira_issue", issue_key: "PROJ-124", ...}
  node_5 → [0.33, 0.91, ...] → metadata: {type: "node", node_id: 5, ...}
```

### 2. Search for Relevant Issues

When generating node content:

```python
# Search for issues similar to node content
issues = await JiraContextService.search_relevant_jira_issues(
    session,
    query_text="Authentication is slow and timing out",
    canvas_id=5,
    user_id=3,
    organization_id=4,
    top_k=5
)

# Returns:
# [
#   {
#     "score": 0.89,
#     "issue_key": "PROJ-123",
#     "title": "Login timeout after 30s",
#     "description": "Users report slow authentication...",
#     "status": "in_progress",
#     "priority": "high",
#     "source_url": "https://...",
#     "task_id": 42
#   },
#   ...
# ]
```

### 3. Format Context for AI

```python
context = JiraContextService.format_jira_context_for_ai(issues)

# Returns formatted markdown:
"""
## Related Jira Issues

The following Jira issues are related to this topic:

### 1. PROJ-123: Login timeout after 30s
**Status**: in_progress | **Priority**: high
**Description**: Users report slow authentication response times...
[View in Jira](https://ogdenventures.atlassian.net/browse/PROJ-123)

### 2. PROJ-124: Database query taking 5+ seconds
**Status**: pending | **Priority**: medium
**Description**: Query optimization needed for user table...
"""
```

### 4. Inject into AI Prompt

Modify the template generation to include Jira context:

```python
# When generating Problem node content
problem_prompt = f"""
You are helping document a product problem.

## Problem Statement
{user_input}

{jira_context}  # ← Injected here!

## Instructions
Write a detailed problem analysis considering:
1. Root causes
2. User impact
3. Related technical issues (reference Jira issues above)
4. Proposed solutions

Format as markdown.
"""
```

### 5. Auto-Link Issues to Node

After AI generation:

```python
# Automatically link high-confidence issues
linked_ids = await JiraContextService.auto_link_relevant_issues(
    session,
    node_id=problem_node.id,
    node_content=generated_content,
    canvas_id=5,
    user_id=3,
    organization_id=4,
    threshold=0.75,  # Only link if >75% similar
    max_links=3
)

# Creates entries in task_node_links table
```

---

## API Endpoints

Add these to `/api/v1/integrations/jira.py`:

### Index Issues
```python
@router.post("/index/{canvas_id}")
async def index_jira_issues_for_canvas(
    canvas_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Index all Jira issues on a canvas for AI context.

    Call this after importing issues from Jira.
    """
    org_id = await get_user_org_id(session, current_user.id)

    result = await JiraContextService.index_jira_issues(
        session, canvas_id, current_user.id, org_id
    )

    return {
        "status": "success",
        "indexed": result["indexed"],
        "message": f"Indexed {result['indexed']} Jira issues for AI context"
    }
```

### Search Issues for Context
```python
@router.post("/search-context")
async def search_jira_context(
    request: JiraContextSearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Search for relevant Jira issues to provide AI context.

    Used internally when generating node content.
    """
    org_id = await get_user_org_id(session, current_user.id)

    issues = await JiraContextService.search_relevant_jira_issues(
        session,
        query_text=request.query,
        canvas_id=request.canvas_id,
        user_id=current_user.id,
        organization_id=org_id,
        top_k=request.top_k or 5
    )

    return {
        "issues": issues,
        "formatted_context": JiraContextService.format_jira_context_for_ai(issues)
    }
```

---

## Frontend Integration

### 1. Auto-Index on Import

Modify `JiraImportDialog.tsx`:

```tsx
const handleImport = async () => {
  // Import issues
  const result = await integrationsApi.importFromJira({ jql, canvasId });

  toast({
    title: 'Import Complete',
    description: `Imported ${result.imported} issues`,
  });

  // Auto-index for AI context
  try {
    await integrationsApi.indexJiraIssuesForCanvas(canvasId);
    toast({
      title: 'Indexed for AI',
      description: 'Issues are now available as AI context',
    });
  } catch (err) {
    console.warn('Failed to index issues:', err);
    // Non-fatal - continue
  }

  onImported();
};
```

### 2. Show Context in Node Editor

When editing a Problem node, show related Jira issues:

```tsx
// In ProblemNodeEditor.tsx
const { data: relatedIssues } = useQuery({
  queryKey: ['jira-context', problemNode.content],
  queryFn: async () => {
    const result = await integrationsApi.searchJiraContext({
      query: problemNode.content,
      canvasId: problemNode.canvas_id,
      topK: 5,
    });
    return result.issues;
  },
  enabled: problemNode.content.length > 20, // Only search if meaningful content
});

// Render in sidebar
<Card>
  <CardHeader>
    <CardTitle className="text-sm">Related Jira Issues</CardTitle>
  </CardHeader>
  <CardContent>
    {relatedIssues?.map(issue => (
      <div key={issue.task_id} className="flex items-center justify-between py-2">
        <div>
          <Badge variant={issue.score > 0.8 ? 'default' : 'outline'}>
            {issue.issue_key}
          </Badge>
          <p className="text-xs truncate">{issue.title}</p>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => linkIssueToNode(issue.task_id, problemNode.id)}
        >
          <Link2 className="h-4 w-4" />
        </Button>
      </div>
    ))}
  </CardContent>
</Card>
```

### 3. AI Generation with Context

When using AI to generate/refine node content:

```tsx
const generateProblemContent = async () => {
  // Search for relevant Jira issues first
  const contextResult = await integrationsApi.searchJiraContext({
    query: userInput,
    canvasId: currentCanvasId,
    topK: 5,
  });

  // Include in AI prompt
  const prompt = `
    Problem: ${userInput}

    ${contextResult.formatted_context}

    Generate a detailed problem analysis...
  `;

  // Call AI generation endpoint with enhanced prompt
  const generated = await aiApi.generateContent({
    node_type: 'problem',
    prompt,
    context: contextResult.issues, // Pass structured data too
  });

  // Auto-link high-confidence issues
  const highConfidenceIssues = contextResult.issues.filter(i => i.score > 0.75);
  for (const issue of highConfidenceIssues) {
    await api.post(`/tasks/${issue.task_id}/link/${problemNode.id}`);
  }

  return generated;
};
```

---

## Enhanced AI Template Integration

Modify the template system to automatically include Jira context:

```python
# app/services/template_service.py

async def resolve_template_with_jira_context(
    session: AsyncSession,
    node_type: str,
    subtype: Optional[str],
    user_id: int,
    organization_id: Optional[int],
    canvas_id: int,
    user_input: str,  # e.g., "Authentication is slow"
) -> dict:
    """
    Resolve template and enrich with Jira context.
    """
    # Get base template
    template = await TemplateService.resolve_template(
        session, node_type, subtype, user_id, organization_id
    )

    # For problem/doc nodes, search for relevant Jira context
    if node_type in ("problem", "doc"):
        jira_issues = await JiraContextService.search_relevant_jira_issues(
            session,
            query_text=user_input,
            canvas_id=canvas_id,
            user_id=user_id,
            organization_id=organization_id,
            top_k=5
        )

        if jira_issues:
            jira_context = JiraContextService.format_jira_context_for_ai(jira_issues)

            # Inject into generation prompt
            template["generation_prompt"] = f"""
{template["generation_prompt"]}

## Contextual Information from Related Jira Issues

{jira_context}

**Important**: Reference relevant Jira issues (e.g., "As noted in PROJ-123...")
when they provide useful context for this problem/solution.
"""

            # Store issues for later auto-linking
            template["related_jira_issues"] = jira_issues

    return template
```

---

## Example User Flow

### Scenario: Creating a Problem Node about Performance

1. **User creates Problem node** on canvas
2. **Types initial content**: "Login page is taking 5+ seconds to load"
3. **System searches Jira** (automatic):
   ```
   Found 3 relevant issues:
   - PROJ-123: Login timeout after 30s (0.91 similarity)
   - PROJ-089: Database query slow for users table (0.83 similarity)
   - PROJ-045: API response time optimization (0.76 similarity)
   ```
4. **Sidebar shows** related issues with confidence scores
5. **User clicks "Generate Details"** (AI button)
6. **System includes Jira context** in prompt:
   ```
   Problem: Login page taking 5+ seconds

   Related Jira Issues:
   - PROJ-123: Users report timeouts...
   - PROJ-089: Database query taking 5s...

   Generate a detailed problem analysis...
   ```
7. **AI generates** enriched content:
   ```markdown
   # Problem: Slow Login Performance

   ## Overview
   Users experience 5+ second load times during authentication...

   ## Root Causes
   Based on investigation in PROJ-123 and PROJ-089:
   - Database query inefficiency in user lookup
   - Missing index on email column
   - API endpoint not using connection pooling

   ## Impact
   - 45% of users abandon login (see PROJ-123 metrics)
   - Peak hour failures increase

   ## Related Issues
   - [PROJ-123](link): Login timeout investigation
   - [PROJ-089](link): Database optimization
   ```
8. **System auto-links** PROJ-123 and PROJ-089 to the node
9. **Node displays** with linked Jira badges

---

## Configuration

### Enable Jira Context Indexing

Add to `.env`:
```bash
# Enable automatic Jira context indexing on import
JIRA_AUTO_INDEX=true

# Minimum similarity threshold for auto-linking
JIRA_AUTO_LINK_THRESHOLD=0.75

# Max issues to include in AI context
JIRA_CONTEXT_MAX_ISSUES=5
```

### Canvas Settings

Allow users to enable/disable per canvas:

```python
# In Canvas model
class Canvas:
    # ... existing fields
    enable_jira_context: bool = True  # Use Jira issues as AI context
    jira_auto_link: bool = True       # Auto-link relevant issues
```

---

## Benefits

### 1. **Automatic Context**
- No manual searching for related Jira issues
- AI aware of existing discussions/bugs
- Reduces duplicate problem documentation

### 2. **Better AI Output**
- AI references actual data (Jira issues) not hypotheticals
- Includes real metrics, timelines, user feedback
- More actionable recommendations

### 3. **Improved Traceability**
- Clear links between canvas nodes and Jira work
- Bidirectional navigation (canvas ↔ Jira)
- Better project visibility

### 4. **Knowledge Reuse**
- Jira becomes living documentation source
- Historical context automatically available
- Institutional knowledge preserved

---

## Testing

```python
# Test indexing
POST /api/v1/integrations/jira/index/5
→ {"indexed": 12, "status": "success"}

# Test search
POST /api/v1/integrations/jira/search-context
{
  "query": "authentication slow",
  "canvas_id": 5,
  "top_k": 3
}
→ {
  "issues": [
    {"score": 0.89, "issue_key": "PROJ-123", ...},
    {"score": 0.82, "issue_key": "PROJ-089", ...}
  ],
  "formatted_context": "## Related Jira Issues\n\n..."
}

# Verify auto-linking
GET /api/v1/tasks?linked_node_id=42
→ [
  {"id": 15, "source_id": "PROJ-123", ...},
  {"id": 16, "source_id": "PROJ-089", ...}
]
```

---

## Next Steps

1. **Implement Backend Service** ✅ (Done - `jira_context_service.py`)
2. **Add API Endpoints** (Add to `endpoints/jira.py`)
3. **Frontend Integration** (Import dialog + Node editor)
4. **Template System Enhancement** (Auto-inject context)
5. **Testing** (Index real Jira data, verify search quality)

Want me to implement the API endpoints next?
