# State Machine Model 


IDLE:
- Ready for chat input
HELP:
- Asking for clarification to improve query 
META:
- Information regarding TextQL system
QUERY:
- Searches for data sources, metrics, tables and dashboards for user question
PLAN:
- Plan mode for creating a structured transition between the states for data
DONE:
- Completed analysis, answer user queries, provide results
CLEANUP:
- Clean and format data using scripts 
ANALYZE:
- Python-based task to extract meaningful patterns, stats and insights
VISUALIZE:
- :w



## Flows 

- Question:
    - IDLE → QUERY → ANALYZE → DONE

- Data Prep:
    - QUERY → REVIEW → CLEANUP → ANALYZE

- Visualization Flow: 
    - ANALYZE → VISUALIZE → DONE

- Clarification Flow:
    - ANY MODE → HELP → RETURN TO PREVIOUS MODE






