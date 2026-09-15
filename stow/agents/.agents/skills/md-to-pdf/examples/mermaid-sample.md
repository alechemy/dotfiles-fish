# Reading diagrams

## A short workflow

```mermaid
flowchart LR
  Source[Markdown] --> Layout[Reading layout]
  Layout --> Edition[PDF edition]
```

The diagram should keep its labels, arrows, and white background without entering the annotation margin.

## An exchange between two participants

```mermaid
sequenceDiagram
  participant Reader
  participant Notebook
  Reader->>Notebook: Open a reading edition
  Notebook-->>Reader: Display the page
  Reader->>Notebook: Underline a sentence
  Reader->>Notebook: Write a marginal note
```

Each diagram renders locally. The source Markdown remains unchanged.
