import csv
import traceback

# --- GLOBAL VARIABLES ---

F_PRINT = open("control.csv", "w")
F_LOG = open("control-debug.log", "w")

# files
PV_PATH = "PV_PRO.csv"
DEMAND_PATH = "BUI_LOAD_norm.csv"
EL_DEC_PATH = "EL_DEC.csv"

DEMANDS_NORM = None
PVS = None
EL_DECS = None

# TIME
STEP = 6/60
CURRENT_STEP = 0.0

# INPUTS with initial values
DAY_OF_YEAR = 0
CURRENT_SOC = 0
CURRENT_PV = 0
CURRENT_EL_TOT = 0
CURRENT_DEMAND = 0
T1_BOT = 0
T_MIX_OUT = 0

# PARAMS (to be decided by user)
PV_SPAN = 4.0 # time period that in each PV production is evaluated (should be almost equal to time to charge the TES)
DECISION_HOUR = 6 # time of the day when decision of TES charging is taken
PREDICTION_HORIZON = 24
MIN_PV = 12600 # threshold of PV production which is equal to minimum power rate of heatpumps
MAX_SOC = 1 # fully charged value of TES SOC in heating mode
MIN_SOC = 0 # fully charged value of TES SOC in cooling mode
SOC_DISCHARGED_HEAT = 0.05 # threshold of fully discharged TES in heating mode (e.g., < 0.05). Value decided based on temperature reported in the deliverable.
SOC_DISCHARGED_COOL = 0.95 # threshold of fully discharged TES in cooling mode (e.g., > 0.95). Value decided based on temperature reported in the deliverable.

# CONSTANTS
TES_OFF_MODE = 0
TES_HEAT_MODE = HEAT_SEASON = 1
TES_COOL_MODE = COOL_SEASON = -1
TES_PUMP_ON = 1
TES_PUMP_OFF = 0

# INTERMEDIATES (values computed at each timestep which are not outputs nor inputs)
START_TES_CHARGE_TIME = -1
SELECTED_TES_CHARGE_MODE = TES_OFF_MODE
CURRENT_PV_OVER = 0

# OUTPUTS
TES_HEAT_ON = 0 # control signal of TES charging in heating mode
TES_HEAT_OFF = 0 # control signal of TES charging in heating mode
SC2 = 0 # regulates the TES PUMP
DEMAND_NORM = 0 # current normalized demand (for the day)


# --- END OF GLOBAL VARIABLES ---

# function called by TRNSYS (Type169)
def main():
   global CURRENT_SOC, DAY_OF_YEAR, CURRENT_PV, CURRENT_DEMAND, CURRENT_EL_TOT, CURRENT_STEP, T1_BOT, T_MIX_OUT
   try:
      timestep = TRNSYS.getSimulationTime() # requires modified Type169 that allows to read the timestep in the Python environment
      
      # avoid executing logic multiple times for timestep because in the deck there are loops and this function will be called multiple times per timestep
      if CURRENT_STEP != timestep: 
         compute() # main logic
         # prints in control.csv
         pprint(CURRENT_STEP, CURRENT_SOC, DAY_OF_YEAR, CURRENT_PV, TES_HEAT_ON, TES_COOL_ON, START_TES_CHARGE_TIME, SELECTED_TES_CHARGE_MODE, SC2, DEMAND_NORM, CURRENT_PV_OVER, CURRENT_DEMAND, T1_BOT, T_MIX_OUT)

      CURRENT_SOC = TRNSYS.getInputValue(1)
      DAY_OF_YEAR = TRNSYS.getInputValue(2)
      CURRENT_PV = TRNSYS.getInputValue(3)
      CURRENT_DEMAND = TRNSYS.getInputValue(4)
      CURRENT_EL_TOT = TRNSYS.getInputValue(5)
      T1_BOT = TRNSYS.getInputValue(6)
      T_MIX_OUT = TRNSYS.getInputValue(7)

      
      CURRENT_STEP = timestep

      TRNSYS.setOutputValue(1, TES_HEAT_ON)
      TRNSYS.setOutputValue(2, TES_COOL_ON)
      TRNSYS.setOutputValue(3, SC2)
      TRNSYS.setOutputValue(4, DEMAND_NORM)

   except Exception as err:
      log(traceback.format_exc()) # logs error
      raise err

# main logic
def compute():
   prepareIntermediates()
   # TES charging time is decided once per day at DECISION_HOUR
   if CURRENT_STEP.is_integer() and CURRENT_STEP % 24 == DECISION_HOUR: 
      decideTESChargingPred()
   setTESMode()
   setPumpMode()

def prepareIntermediates():
   global DEMAND_NORM, CURRENT_PV_OVER
   if DAY_OF_YEAR != 0:
      DEMAND_NORM = DEMANDS_NORM[DAY_OF_YEAR]
   CURRENT_PV_OVER = max(0, CURRENT_PV - CURRENT_EL_TOT)

# Prediction mode: pick the best time period (PV_SPAN) in the next 24h to charge the TES
# Decides when to charge the TES (START_TES_CHARGE_TIME) and in which mode (SELECTED_TES_CHARGE_MODE)
def decideTESChargingPred():
   global START_TES_CHARGE_TIME, SELECTED_TES_CHARGE_MODE
   # time variable to check from CURRENT_STEP to CURRENT_STEP + PREDICTION HORIZON (e.g., 24h)
   time = CURRENT_STEP
   endtime = time + PREDICTION_HORIZON
   # the current highest value of PV production in each PV_SPAN (e.g., 4h)
   best_pv = 0
   # the time when highest PV production starts (time to charge the TES)
   best_time = None
   while time < endtime:
      offset = 0
      sum_pv = 0
      # sum of PV overproduction of each time step of PV_SPAN 
      while offset < PV_SPAN:
         timestepkey = getTimeStepKey(time+offset)
         if timestepkey not in PVS or timestepkey not in EL_DECS:
            sum_pv = -1
            log("key not in", timestepkey)
            break
         pv_predicted = PVS[timestepkey]
         el_dec_predicted = EL_DECS[timestepkey]
         pv_over_predicted = pv_predicted - (el_dec_predicted + MIN_PV)
         # if pv_over_predicted is less than zero we dont have overproduction and we discard this timestep
         if pv_over_predicted < 0:
            sum_pv = -1
            break
         sum_pv += pv_over_predicted
         offset += STEP
      if sum_pv > best_pv:
         best_time = time 
         best_pv = sum_pv
      time += STEP

   # decision
   if best_time:
      START_TES_CHARGE_TIME = best_time
      SELECTED_TES_CHARGE_MODE = TES_HEAT_MODE if getDemandType() == HEAT_SEASON else TES_COOL_MODE
   else:
      START_TES_CHARGE_TIME = -1
      SELECTED_TES_CHARGE_MODE = TES_OFF_MODE

# set the TES control signal at each time step (action, not decision)
def setTESMode():
   global TES_COOL_ON, TES_HEAT_ON, START_TES_CHARGE_TIME, SELECTED_TES_CHARGE_MODE
   
   TES_COOL_ON = TES_HEAT_ON = 0
   
   # if we are in the time to charge the TES and TES is not fully charged
   if CURRENT_STEP >= START_TES_CHARGE_TIME and CURRENT_STEP <= START_TES_CHARGE_TIME + PV_SPAN:
      if SELECTED_TES_CHARGE_MODE == TES_HEAT_MODE and CURRENT_SOC <= MAX_SOC:
         TES_HEAT_ON = 1
      elif SELECTED_TES_CHARGE_MODE == TES_COOL_MODE and CURRENT_SOC >= MIN_SOC:
         TES_COOL_ON = 1
      
   # stop charging if fully charged
   if SELECTED_TES_CHARGE_MODE == TES_HEAT_MODE and CURRENT_SOC > MAX_SOC:
      START_TES_CHARGE_TIME = -1 
      SELECTED_TES_CHARGE_MODE = TES_HEAT_OFF
   elif SELECTED_TES_CHARGE_MODE == TES_COOL_MODE and CURRENT_SOC < MIN_SOC:
      START_TES_CHARGE_TIME = -1
      SELECTED_TES_CHARGE_MODE = TES_HEAT_OFF


def setPumpMode():
   global SC2
   
   soc_discharged = CURRENT_SOC <= SOC_DISCHARGED_HEAT if getDemandType() == HEAT_SEASON else CURRENT_SOC >= SOC_DISCHARGED_COOL
   # in heating TES is discharged if the temperature inside the TES is lower than distribution temperature (viceversa in cooling)
   temp_discharged = T1_BOT <= T_MIX_OUT if getDemandType() == HEAT_SEASON else T1_BOT >= T_MIX_OUT

   if TES_HEAT_ON or TES_COOL_ON: # pump open in CHARGING MODE
      SC2 = TES_PUMP_ON
   elif not CURRENT_PV_OVER and CURRENT_DEMAND and not soc_discharged and not temp_discharged: # pump open in DISCHARGING MODE for user needs
      SC2 = TES_PUMP_ON
   else:
      SC2 = TES_PUMP_OFF
   
   
def getTimeStepKey(timestep):
   return int(float(timestep)*10)

def getDemandType():
   return HEAT_SEASON if DEMAND_NORM >= 0.5 else COOL_SEASON

def setupFiles():
   global DEMANDS_NORM, PVS, EL_DECS
   F_DEMAND = open(DEMAND_PATH, mode='r')
   F_DEMAND = csv.reader(F_DEMAND)
   DEMANDS_NORM = {int(rows[0]):float(rows[1]) for rows in F_DEMAND}

   F_PV = open(PV_PATH, mode='r')
   F_PV = csv.reader(F_PV)
   PVS = {getTimeStepKey(rows[0]):float(rows[1]) for rows in F_PV}

   F_EL_DEC = open(EL_DEC_PATH, mode='r')
   F_EL_DEC = csv.reader(F_EL_DEC)
   EL_DECS = {getTimeStepKey(rows[0]):float(rows[1]) for rows in F_EL_DEC}

def log(*args):
   F_LOG.write(",".join([str(a) for a in args])+"\n")
   F_LOG.flush()

def pprint(*args):
   F_PRINT.write(",".join([str(a) for a in args])+"\n")
   F_PRINT.flush()

# initialization
setupFiles()
pprint("CURRENT_STEP,CURRENT_SOC,DAY_OF_YEAR,CURRENT_PV,TES_HEAT_ON,TES_COOL_ON,START_TES_CHARGE_TIME,SELECTED_TES_CHARGE_MODE,SC2,DEMAND_NORM,CURRENT_PV_OVER,CURRENT_DEMAND,T1_BOT,T_MIX_OUT")



### --- DEBUG SECTION !DO NOT TOUCH! ---

class TRNSYS_MOCK:
   i = 6.0
   def getInputValue(self, *args):
      return 10
   def setOutputValue(self,*args):
      pass
   def getSimulationTime(self):
      self.i += 1
      return self.i

# settings DANGEROUS. DO NOT CHANGE. SHOULD BE FALSE
DEBUG = False
if DEBUG:   
   TRNSYS = TRNSYS_MOCK()
else:
   import TRNSYSpy as TRNSYS

if DEBUG:
   main()
   main()