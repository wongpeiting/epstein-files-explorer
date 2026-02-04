# Epstein Files Explorer

An experiment in building a document exploration dashboard using [Claude Code](https://claude.ai/claude-code), Anthropic's AI-powered coding assistant.

## About This Project

This project was built collaboratively with Claude Code to create an interactive frontend for exploring the Epstein document corpus. The entire dashboard—HTML, CSS, JavaScript, and D3.js visualizations—was developed through conversation with Claude Code, demonstrating how AI can assist in rapid prototyping of data visualization tools.

## Data Sources

This dashboard searches across **two document releases**:

### 1. November 2025 — House Oversight Committee Release
- **API**: [cjc0013/epstein-corpus-explorer](https://huggingface.co/spaces/cjc0013/epstein-corpus-explorer) (HuggingFace)
- **Documents**: ~330,000 documents including emails, court filings, flight logs, financial records, and communications spanning 2002–2019
- **Features**: Full-text search, 256 semantic clusters for exploration
- **Note**: This is a frontend-only application that calls the Gradio API. No data is stored locally.

### 2. January 2026 — DOJ Release
- **Source**: [promexdotme/epstein-justice-files-text](https://github.com/promexdotme/epstein-justice-files-text) (GitHub)
- **Documents**: 1,076 text files extracted from the DOJ release
- **Features**: Filename search, click to view full document content
- **Note**: This is a subset of the full DOJ release (3+ million pages). The text files are fetched directly from GitHub.

## How to Use

### Search
- Type names, places, or keywords in the search bar
- Results show document previews with matching text highlighted
- Click any result to view the full document

### Filter by Topic
- Use the sidebar tags to filter by predefined topics (People, Places, Organizations, Topics)
- Click multiple tags to combine filters (AND logic)
- Active filters appear above results; click "Clear all" to reset

### Browse by Cluster
- Documents are grouped into 256 clusters based on content similarity
- The force-directed visualization shows all clusters as interactive nodes
- Larger nodes contain more documents
- Click any cluster to explore its documents
- Drag nodes to rearrange the visualization

### Navigation
- **Header stats**: Click "Clusters: 256" to view the cluster overview
- **Sidebar**: Toggle sections to show/hide topic categories
- **Results**: Scroll to load more results automatically

## Live Site

Visit: [https://wongpeiting.github.io/epstein-files-explorer/](https://wongpeiting.github.io/epstein-files-explorer/)

## Technical Details

- **Frontend**: Pure HTML/CSS/JavaScript (no build step)
- **Visualization**: D3.js for force-directed cluster layout
- **API**: Gradio SSE (Server-Sent Events) for async data fetching
- **Hosting**: GitHub Pages (static site)

## Disclaimer

This tool is provided for research and educational purposes. The documents in this corpus are public records. This project is not affiliated with any government agency or the original data providers.

## License

MIT
