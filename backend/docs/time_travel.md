# Key notes about handling the worldline


We want to ensure that the states/events are immutable
- This will allow us to create branches off of events
- Rebuilding long history with optional checkpointing 
    - What we might want to do is auto checkpoint
                Current_Branch
                /          
New Worldline (Becomes the current branch)

- We can store the pointers for the branching and use the state that way

# Rendering the worldline
```python

get_worldline(worldline):
    events = []
    curr_wordline = worldline

    while curr_wordline:
        events += events_in_worldline(curr_worldline)
        curr_worldline = parent_worldline

    return merged_until_fork_point
```




            
