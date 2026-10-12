import pandas as pd
import numpy as np
from optimization import *
import matplotlib.pyplot as plt

# Constants
DATA = pd.read_excel(ROOT_DIR + OUTPUT_FILENAME)
# Functions
def towns_total_cost(df):

    # Group by 'town' and sum the costs for each town
    total_cost_by_town = df.groupby('city')['cost'].sum().sort_values()

    # Return closest 75th percentile town

def top_10_expensive_towns_total_cost(df):
    """
    Function to return the top 10 cheapest towns based on the sum of costs for each town.

    Args:
    df (DataFrame): The input DataFrame.

    Returns:
    DataFrame: A DataFrame containing the top 10 cheapest towns.
    """
    # Group by 'town' and sum the costs for each town
    total_cost_by_town = df.groupby('city')['cost'].sum().sort_values(ascending=False).head(10)

    # Filter the original dataframe to include only the towns from the top 10 cheapest list
    top_10_towns_filtered = df[df['city'].isin(total_cost_by_town.index)]

    return top_10_towns_filtered


def convert_string_to_list(df):
    """
    Convert string representations of lists into actual lists for all dataframe elements.

    Args:
    df (DataFrame): The input DataFrame.

    Returns:
    DataFrame: A DataFrame with string lists converted to actual lists.
    """
    for column in df:
        for idx, value in df[column].items():
            # Check if the value is a string that represents a list
            if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
                try:
                    # Convert the string to a list
                    df.at[idx, column] = eval(value)
                except:
                    # If the conversion fails, keep the original value
                    pass
    return df

CLEANED_DATA = convert_string_to_list(DATA)
# Exclude all rows with the town called 'Pryor'
CLEANED_DATA = CLEANED_DATA[~CLEANED_DATA['city'].isin(['Pryor'])]

def get_generation_data(year, matching, technology, grouped_data=CLEANED_DATA):
    """
    Get the indices of rows with the minimum and maximum cost for a specific group in the grouped data.

    Args:
    grouped_data (GroupBy object): The grouped DataFrame.
    year (int): The year to filter the group.
    matching (str): The matching criteria to filter the group.
    technology (str): The technology type to filter the group.

    Returns:
    tuple: A tuple containing the indices of the min and max cost rows.
    """
    grouped_data = grouped_data.groupby(['year', 'matching', 'technology'])
    try:
        group = grouped_data.get_group((year, matching, technology))
        idx_min = group['cost'].idxmin()
        idx_max = group['cost'].idxmax()
        return group.loc[idx_min], group.loc[idx_max]
    except KeyError:
        print("Group not found for the specified year, matching, and technology.")
        return None, None


def get_closest_quantile_data(year, matching, technology, grouped_data=CLEANED_DATA):
    """
    Get the rows that are closest to the Q1, median, and Q3 of the cost for a specific group in the grouped data.

    Args:
    grouped_data (GroupBy object): The grouped DataFrame.
    year (int): The year to filter the group.
    matching (str): The matching criteria to filter the group.
    technology (str): The technology type to filter the group.

    Returns:
    tuple: A tuple containing the rows closest to Q1, median, and Q3 cost.
    """
    grouped_data = grouped_data.groupby(['year', 'matching', 'technology'])

    try:
        group = grouped_data.get_group((year, matching, technology))
        q1 = group['cost'].quantile(0.25)
        median = group['cost'].median()
        q3 = group['cost'].quantile(0.75)

        idx_closest_q1 = (group['cost'] - q1).abs().idxmin()
        idx_closest_median = (group['cost'] - median).abs().idxmin()
        idx_closest_q3 = (group['cost'] - q3).abs().idxmin()

        return group.loc[idx_closest_q1], group.loc[idx_closest_q3], group.loc[idx_closest_median]
    except KeyError:
        print("Group not found for the specified year, matching, and technology.")
        return None, None, None


def return_masked_data(time, matching, tech, data=CLEANED_DATA):
    mask = (data['year'] == time) & (data['matching'] == matching) & (data['technology'] == tech)
    new_data = data[mask]
    randomizer = np.random.randint(0, len(new_data))
    return new_data.iloc[randomizer]

def process_data(year, matching, technology, df = CLEANED_DATA):
    # Group by 'town' and sum the costs
    total_cost_by_town = df.groupby('city')['cost'].sum().sort_values()

    # Find the town names for the 25th, 50th, and 75th percentiles
    percentiles = total_cost_by_town.quantile([0.25, 0.5, 0.75])

    towns_at_percentiles = [total_cost_by_town.sub(p).abs().idxmin() for p in percentiles]

    # Filter the dataframe for these towns
    filtered_df = df[df['city'].isin(towns_at_percentiles)]

    # Group by 'year', 'matching', and 'technology'
    grouped = filtered_df.groupby(['year', 'matching', 'technology'])

    return grouped.get_group((year, matching, technology))

# Running
if __name__ == '__main__':
   print(return_masked_data(2027, 'hourly', 'AP CCS'))




