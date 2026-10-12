#scenarios = ['A','B', 'C', 'D', 'New']
scenarios = ['New']
technologies = ['AP SMR', 'AP CCS','AP BH2S', 'AP AEC']
#technologies = ['AP SMR', 'AP CCS', 'AP BH2S']
#times = [2023, 2030]
times = [2027, 2040]
starts = [0, (times[1] - times[0])*12]
matching_type = ['yearly', 'monthly', 'hourly']
policies = [True, False]
L = 40*12

if __name__ == '__main__':
    print(starts)