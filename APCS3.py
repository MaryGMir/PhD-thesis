import csv
import traceback

# --- GLOBAL VARIABLES ---

F_PRINT = open("control.csv", "w")
ST_ON_PRINT = open("st_on.csv", "w")
F_LOG = open("control-debug.log", "w")

# files
PV_PATH = "PV_PRO.csv"
DEMAND_PATH = "BUI_LOAD_day.csv"
EL_DEC_PATH = "EL_DEC.csv"

DEMANDS_DAY = None
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
TOTAL_Q_TES = 0

# PARAMS (to be decided by user)
PV_SPAN_MIN = 0.5
DECISION_HOUR = 6 # time of the day when decision of TES charging is taken
PREDICTION_HORIZON = 24
MIN_PV = 12600 # threshold of PV production which is equal to minimum power rate of heatpumps
MAX_SOC = 1 # fully charged value of TES SOC in heating mode
MIN_SOC = 0 # fully charged value of TES SOC in cooling mode
SOC_DISCHARGED_HEAT = 0.05 # threshold of fully discharged TES in heating mode (e.g., < 0.05). Value decided based on temperature reported in the deliverable.
SOC_DISCHARGED_COOL = 0.95 # threshold of fully discharged TES in cooling mode (e.g., > 0.95). Value decided based on temperature reported in the deliverable.
MAX_Q_TES = 169
MIN_Q_TES = 40

# CONSTANTS
TES_OFF_MODE = 0
TES_HEAT_MODE = HEAT_SEASON = 1
TES_COOL_MODE = COOL_SEASON = -1
TES_PUMP_ON = 1
TES_PUMP_OFF = 0
SIMUL_END = 9504.0
# INTERMEDIATES (values computed at each timestep which are not outputs nor inputs)
ST_ON = {}
SELECTED_TES_CHARGE_MODE = TES_OFF_MODE
CURRENT_PV_OVER = 0
SEA_TOTAL_Q_TES = 0 # seasonal Q in TES
TARGET_SOC = 0


# OUTPUTS
TES_HEAT_ON = 0 # control signal of TES charging in heating mode
TES_HEAT_OFF = 0 # control signal of TES charging in heating mode
SC2 = 0 # regulates the TES PUMP
DEMAND_DAY = 0 # daily demand 


# --- END OF GLOBAL VARIABLES ---

# function called by TRNSYS (Type169)
def main():
   global CURRENT_SOC, DAY_OF_YEAR, CURRENT_PV, CURRENT_DEMAND, CURRENT_EL_TOT, CURRENT_STEP, T1_BOT, T_MIX_OUT, TOTAL_Q_TES
   try:
      timestep = TRNSYS.getSimulationTime() # requires modified Type169 that allows to read the timestep in the Python environment
      # avoid executing logic multiple times for timestep because in the deck there are loops and this function will be called multiple times per timestep
      if CURRENT_STEP != timestep: 
         compute() # main logic
         # prints in control.csv
         pprint(CURRENT_STEP, CURRENT_SOC, DAY_OF_YEAR, CURRENT_PV, TES_HEAT_ON, TES_COOL_ON, SELECTED_TES_CHARGE_MODE, SC2, DEMAND_DAY, CURRENT_PV_OVER, CURRENT_DEMAND, T1_BOT, T_MIX_OUT, TOTAL_Q_TES, SEA_TOTAL_Q_TES, TARGET_SOC)
      CURRENT_SOC = TRNSYS.getInputValue(1)
      DAY_OF_YEAR = TRNSYS.getInputValue(2)
      CURRENT_PV = TRNSYS.getInputValue(3)
      CURRENT_DEMAND = TRNSYS.getInputValue(4)
      CURRENT_EL_TOT = TRNSYS.getInputValue(5)
      T1_BOT = TRNSYS.getInputValue(6)
      T_MIX_OUT = TRNSYS.getInputValue(7)
      TOTAL_Q_TES = TRNSYS.getInputValue(8)

      CURRENT_STEP = timestep

      TRNSYS.setOutputValue(1, TES_HEAT_ON)
      TRNSYS.setOutputValue(2, TES_COOL_ON)
      TRNSYS.setOutputValue(3, SC2)
      TRNSYS.setOutputValue(4, DEMAND_DAY)

   except Exception as err:
      log(traceback.format_exc()) # logs error
      log(ST_ON)
      raise err

# main logic
def compute():
   prepareIntermediates()
   # TES charging time is decided once per day at DECISION_HOUR
   print(CURRENT_STEP)
   if CURRENT_STEP.is_integer() and CURRENT_STEP % 24 == DECISION_HOUR: 
      decideTESChargingPred()
   setTESMode()
   setPumpMode()


def prepareIntermediates():
   global DEMAND_DAY, CURRENT_PV_OVER, SEA_TOTAL_Q_TES, TARGET_SOC
   if DAY_OF_YEAR != 0:
      DEMAND_DAY = DEMANDS_DAY[DAY_OF_YEAR]
   CURRENT_PV_OVER = max(0, CURRENT_PV - CURRENT_EL_TOT)
 
   SEA_TOTAL_Q_TES = TOTAL_Q_TES - MIN_Q_TES if getDemandType() == HEAT_SEASON else MAX_Q_TES - TOTAL_Q_TES # from 0 to MAX_Q_TES-MIN_Q_TES
   TARGET_SOC = (1 + DEMAND_DAY/(MAX_Q_TES-MIN_Q_TES))/2
   TARGET_SOC = min(max(MIN_SOC, TARGET_SOC), MAX_SOC)

# Prediction mode: pick the best time period (PV_SPAN) in the next 24h to charge the TES
# Decides when to charge the TES and in which mode (SELECTED_TES_CHARGE_MODE)
def decideTESChargingPred():
   global SELECTED_TES_CHARGE_MODE, ST_ON
   # time variable to check from CURRENT_STEP to CURRENT_STEP + PREDICTION HORIZON (e.g., 24h)
   time = CURRENT_STEP
   endtime = min(time + PREDICTION_HORIZON, SIMUL_END)
   ST_ON = {}
   while time < endtime:
      offset = 0
      # sum of PV overproduction of each time step of PV_SPAN 
      while time + offset < endtime:
         timestepkey = getTimeStepKey(time+offset)
         if timestepkey not in PVS or timestepkey not in EL_DECS:
            sum_pv = -1
            log("key not in", timestepkey)
            break
         log(timestepkey, time)
         pv_predicted = PVS[timestepkey]
         el_dec_predicted = EL_DECS[timestepkey]
         pv_over_predicted = pv_predicted - (el_dec_predicted + MIN_PV)
         # if pv_over_predicted is less than zero we dont have overproduction and we discard this timestep
         if pv_over_predicted < 0:
            break
         offset += STEP
      starttime = time
      while getTimeStepKey(time) < getTimeStepKey(starttime+offset) and time < endtime:
         decision = offset >= PV_SPAN_MIN
         ST_ON[getTimeStepKey(time)] = decision
         pston(time, decision)
         time += STEP
      
      time = min(time, endtime)
      timestepkey = getTimeStepKey(time)
      log(timestepkey, time)
      ST_ON[timestepkey] = False
      pston(time, False)
      time += STEP
   
   SELECTED_TES_CHARGE_MODE = TES_HEAT_MODE if getDemandType() == HEAT_SEASON else TES_COOL_MODE
 

# set the TES control signal at each time step (action, not decision)
def setTESMode():
   global TES_COOL_ON, TES_HEAT_ON, ST_ON, SELECTED_TES_CHARGE_MODE
   
   TES_COOL_ON = TES_HEAT_ON = 0
   
   # if we are in the time to charge the TES and TES is not fully charged
   if ST_ON and ST_ON[getTimeStepKey(CURRENT_STEP)]:
      if SELECTED_TES_CHARGE_MODE == TES_HEAT_MODE and CURRENT_SOC <= TARGET_SOC:
         TES_HEAT_ON = 1
      elif SELECTED_TES_CHARGE_MODE == TES_COOL_MODE and CURRENT_SOC >= TARGET_SOC:
         TES_COOL_ON = 1
      
   # stop charging if fully charged
   if SELECTED_TES_CHARGE_MODE == TES_HEAT_MODE and CURRENT_SOC > TARGET_SOC:
      ST_ON = {}
      SELECTED_TES_CHARGE_MODE = TES_HEAT_OFF
   elif SELECTED_TES_CHARGE_MODE == TES_COOL_MODE and CURRENT_SOC < TARGET_SOC:
      ST_ON = {}
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
   return int(round(float(timestep)*10,1))

def getDemandType():
   return HEAT_SEASON if DEMAND_DAY >= 0 else COOL_SEASON

def setupFiles():
   global DEMANDS_DAY, PVS, EL_DECS
   F_DEMAND = open(DEMAND_PATH, mode='r')
   F_DEMAND = csv.reader(F_DEMAND)
   DEMANDS_DAY = {int(rows[0]):float(rows[1]) for rows in F_DEMAND}

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

def logston(*args):
   ST_ON_PRINT.write(",".join([str(a) for a in args])+"\n")
   ST_ON_PRINT.flush()

def pston(time, decision):
   timestepkey = getTimeStepKey(time)
   pv_predicted = PVS[timestepkey]
   el_dec_predicted = EL_DECS[timestepkey]
   pv_over_predicted = max(0, pv_predicted - (el_dec_predicted + MIN_PV))
   logston(time, decision, pv_over_predicted, pv_predicted, el_dec_predicted)
 

# initialization
setupFiles()
pprint("CURRENT_STEP,CURRENT_SOC,DAY_OF_YEAR,CURRENT_PV,TES_HEAT_ON,TES_COOL_ON,SELECTED_TES_CHARGE_MODE,SC2,DEMAND_DAY,CURRENT_PV_OVER,CURRENT_DEMAND,T1_BOT,T_MIX_OUT,TOTAL_Q_TES,SEA_TOT_Q_TES,TARGET_SOC")
logston("TIME,ST_ON,PV_OVER_PRED,PV_PRED,EL_DEC_PRED")


### --- DEBUG SECTION !DO NOT TOUCH! ---

class TRNSYS_MOCK:
   i = 0.0
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
   for i in range(9504):
      main()
 