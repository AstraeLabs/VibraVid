# 19.06.24

# External library
from rich.console import Console


# Variable
console = Console()
available_colors = ['red', 'magenta', 'yellow', 'cyan', 'green', 'blue', 'white']
column_to_hide = ['Slug', 'Sub_ita', 'Last_air_date', 'Seasons_count', 'Url', 'Image', 'Path_id']


def get_select_title(table_show_manager, media_search_manager, num_results_available): 
    """
    Display a selection of titles and prompt the user to choose one.

    Parameters:
        table_show_manager: Manager for console table display.
        media_search_manager: Manager holding the list of media items.
        num_results_available (int): The number of media items available for selection.

    Returns:
        MediaItem: The selected media item, or None if no selection is made or an error occurs.
    """
    if not media_search_manager.media_list:
        return None

    if not media_search_manager.media_list:
        console.print("\n[red]No media items available.")
        return None
    
    first_media_item = media_search_manager.media_list[0]
    column_info = {"Index": {'color': available_colors[0]}}

    color_index = 1
    for key in first_media_item.__dict__.keys():

        if key.capitalize() in column_to_hide:
            continue

        if key in ('id', 'type', 'name', 'score'):
            if key == 'type': 
                column_info["Type"] = {'color': 'yellow'}

            elif key == 'name': 
                column_info["Name"] = {'color': 'magenta'}
            elif key == 'score': 
                column_info["Score"] = {'color': 'cyan'}
                
        else:
            column_info[key.capitalize()] = {'color': available_colors[color_index % len(available_colors)]}
            color_index += 1

    table_show_manager.clear() 
    table_show_manager.add_column(column_info)

    for i, media in enumerate(media_search_manager.media_list):
        media_dict = {'Index': str(i)}
        for key in first_media_item.__dict__.keys():
            if key.capitalize() in column_to_hide:
                continue
            media_dict[key.capitalize()] = str(getattr(media, key))
        table_show_manager.add_tv_show(media_dict)

    last_command_str = table_show_manager.run(force_int_input=True, max_int_input=len(media_search_manager.media_list))
    table_show_manager.clear()

    if last_command_str is None or last_command_str.lower() in ["q", "quit"]: 
        console.print("\n[red]Selezione annullata o uscita.")
        return None 

    try:
        
        selected_index = int(last_command_str)
        
        if 0 <= selected_index < len(media_search_manager.media_list):
            return media_search_manager.get(selected_index)
            
        else:
            console.print("\n[red]Indice errato o non valido.")
            return None
            
    except ValueError:
        console.print("\n[red]Input non numerico ricevuto dalla tabella.")
        return None