## Diagrams and structured content

If the AI content includes a diagram (or diagram source such as Mermaid, PlantUML, SVG, or a text description of one), the same asymmetry applies in reverse: models do best with the source, humans with the render.

- Render or recommend rendering; never hand a person raw Mermaid as the deliverable.
- Enforce a consistent flow direction (left to right or top to bottom) and one relationship per edge.
- Add a legend if color or shape carries meaning; never rely on color alone.
- If there are more than roughly two dozen elements, split into an overview plus detail views.
- Label nodes in plain language and cut labels that only restate the shape.
