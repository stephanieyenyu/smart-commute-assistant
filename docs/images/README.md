Diagram exports and screenshots.

Required filenames, referenced by the README and docs/:

    architecture.png            <- docs/diagrams/system-architecture.drawio
    reminder-state-machine.png  <- docs/diagrams/reminder-state-machine.drawio
    er-diagram.png              <- docs/diagrams/er-diagram.drawio
    decision-flow.png           <- docs/diagrams/decision-flow.drawio
    demo.gif                    <- full commute cycle, silent, looping

Export from draw.io: File > Export as > PNG, zoom 200%, transparent background off.
Regenerate the .drawio sources with `python docs/diagrams/gen_diagrams.py`.
