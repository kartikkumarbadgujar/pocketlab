from math import sqrt


def estimate_impact(monthly_active_users: int, population_share: float, failures: int, attempts: int) -> dict:
    """Transparent plug-in estimate; Wilson interval models uncertainty in failure probability."""
    if attempts <= 0 or not 0 <= failures <= attempts or not 0 <= population_share <= 1:
        raise ValueError("invalid impact inputs")
    rate = failures / attempts
    z = 1.96
    denom = 1 + z*z/attempts
    center = (rate + z*z/(2*attempts))/denom
    margin = z*sqrt((rate*(1-rate)/attempts)+(z*z/(4*attempts*attempts)))/denom
    scale = monthly_active_users * population_share
    return {"estimated_failure_rate":rate,"estimated_affected_users":scale*rate,"uncertainty":{"method":"95% Wilson interval for failure rate","failure_rate_95ci":[max(0,center-margin),min(1,center+margin)],"affected_users_95ci":[scale*max(0,center-margin),scale*min(1,center+margin)]}}
